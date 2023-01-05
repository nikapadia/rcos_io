import re
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save, pre_save
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone

from portal.services import discord


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Semester(TimestampedModel):
    id = models.CharField(
        max_length=len("202201"),
        primary_key=True,
        help_text="The unique ID of the semseter in RPI's format of YYYYMM where YYYY is the starting year and MM is the starting month.",
    )
    name = models.CharField(
        max_length=30, help_text="User-facing name of semester, e.g. Fall 2022"
    )
    is_accepting_new_projects = models.BooleanField(
        "accepting new projects?",
        default=False,
        help_text="Whether new projects can be proposed for this semester",
    )
    start_date = models.DateField(
        "first day",
        help_text="The first day of the semester according to the RPI Academic Calendar: https://info.rpi.edu/registrar/academic-calendar",
    )
    end_date = models.DateField(
        "last day",
        help_text="The last day of the semester according to the RPI Academic Calendar: https://info.rpi.edu/registrar/academic-calendar",
    )

    @classmethod
    def get_active(cls):
        now = timezone.now().date()
        return cls.objects.filter(start_date__lte=now, end_date__gte=now).first()

    @property
    def enrollment_count(self):
        return self.enrollments.count()

    @property
    def project_count(self):
        return self.enrollments.distinct("project").order_by().count()

    @property
    def is_active(self):
        now = timezone.now().date()
        return self.start_date <= now <= self.end_date

    def get_admins(self):
        return self.enrollments.filter(
            Q(is_coordinator=True) | Q(is_faculty_advisor=True)
        ).order_by("is_faculty_advisor")

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["-start_date"]


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        """Create and save a User with the given email and password."""
        if not email:
            raise ValueError("The given email must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular User with the given email and password."""
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        """Create and save a SuperUser with the given email and password."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser, TimestampedModel):
    RPI = "rpi"
    EXTERNAL = "external"
    ROLE_CHOICES = ((RPI, "RPI"), (EXTERNAL, "External"))

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    username = None
    email = models.EmailField("primary email address", unique=True)
    is_approved = models.BooleanField(
        "approved?",
        default=False,
        help_text="Identity is verified and can participate in RCOS",
    )
    role = models.CharField(choices=ROLE_CHOICES, max_length=30, default=EXTERNAL)

    # Set for RPI users only
    rcs_id = models.CharField(
        blank=True,
        max_length=30,
        help_text="If the user is an RPI user, their RCS ID.",
        verbose_name="RCS ID",
        unique=True,
    )
    graduation_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If the user is an RPI user, their graduation year.",
    )

    # Account integrations
    discord_user_id = models.CharField(
        blank=True,
        max_length=200,
        help_text="The user's Discord account ID from the Discord API",
    )
    github_username = models.CharField(
        blank=True, max_length=200, help_text="The user's GitHub username (not user ID)"
    )

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or "Unnamed User"

    @property
    def display_name(self):
        chunks = []

        if self.first_name:
            chunks.append(self.first_name)
        if self.last_name:
            chunks.append(self.last_name[0])

        if self.role == User.RPI:
            if self.graduation_year:
                chunks.append(f"'{str(self.graduation_year)[2:]}")
            if len(chunks) > 0 and self.rcs_id:
                chunks.append(f"({self.rcs_id})")
            elif self.rcs_id:
                chunks.append(self.rcs_id)

        if len(chunks) == 0:
            chunks.append(self.email)

        return " ".join(chunks).strip()

    @property
    def is_setup(self):
        return (
            self.first_name
            and self.last_name
            and self.github_username
            and self.discord_user_id
        )

    def get_discord_user(self):
        return discord.get_user(self.discord_user_id) if self.discord_user_id else None

    def get_active_semesters(self):
        return (
            Semester.objects.filter(enrollments__user=self.id)
            .order_by("-start_date")
            .distinct()
        )

    def can_propose_project(self, semester: Semester) -> bool:
        if not self.is_approved or not self.is_active:
            return False

        if (
            not semester
            or not semester.is_active
            or not semester.is_accepting_new_projects
        ):
            return False

        # Users can only propose a project if they aren't currently enrolled in a project
        # and can own a max of 4 at one time
        if self.owned_projects.count() >= 4:
            return False
        try:
            if Enrollment.objects.get(user=self, semester=semester).project:
                return False
        except Enrollment.DoesNotExist:
            return True

        return True

    def get_absolute_url(self):
        return reverse("users_detail", args=[str(self.id)])

    def __str__(self) -> str:
        return self.display_name

    objects = UserManager()

    def clean(self):
        if self.role != User.RPI and self.graduation_year is not None:
            raise ValidationError("Only RPI users can have a graduation year set.")

    class Meta:
        ordering = ["first_name", "last_name", "email"]


def pre_save_user(instance, sender, *args, **kwargs):
    if instance._state.adding and instance.email.endswith("@rpi.edu"):
        instance.role = User.RPI
        instance.is_approved = True
        instance.rcs_id = instance.email.removesuffix("@rpi.edu").lower()


pre_save.connect(pre_save_user, sender=User)


class ProjectTag(TimestampedModel):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name


class Project(TimestampedModel):
    slug = models.SlugField()
    name = models.CharField(
        max_length=100, unique=True, help_text="The project's unique name"
    )
    owner = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="owned_projects",
        help_text="The user that can make edits to the project",
    )
    is_approved = models.BooleanField(
        "approved?",
        default=False,
        help_text="Whether the project has been approved by Mentors/Coordinators to participate in RCOS",
    )
    summary = models.CharField(
        max_length=100, help_text="A one-line summary of the project"
    )

    external_chat_url = models.URLField(
        blank=True, help_text="Optional URL to an external chat that this project uses"
    )

    homepage_url = models.URLField(
        blank=True,
        help_text="Optional URL to a homepage for the project, potentially where it is publicly deployed or to documentation",
    )

    tags = models.ManyToManyField(ProjectTag, blank=True, related_name="projects")

    discord_role_id = models.CharField(max_length=200, blank=True)

    discord_text_channel_id = models.CharField(max_length=200, blank=True)

    discord_voice_channel_id = models.CharField(max_length=200, blank=True)

    @property
    def discord_text_channel_url(self):
        if self.discord_text_channel_id:
            return f"https://discord.com/channels/{settings.DISCORD_SERVER_ID}/{self.discord_text_channel_id}"
        return None

    def get_semester_count(self):
        return (
            Project.objects.filter(id=self.id, enrollments__semester__isnull=False)
            .order_by("enrollments__semester")
            .distinct("enrollments__semester")
            .values("enrollments__semester_id")
            .count()
        )

    def get_active_semesters(self):
        return (
            Semester.objects.filter(enrollments__project=self.id)
            .order_by("-start_date")
            .distinct()
        )

    def get_absolute_url(self):
        return reverse("projects_detail", kwargs={"slug": self.slug})

    def is_seeking_members(self, semester: Semester) -> Optional["ProjectPitch"]:
        return self.pitches.filter(semester=semester, project=self).first()

    def save(self, *args, **kwargs):
        if not self.slug or self.slug != slugify(self.name):
            self.slug = slugify(self.name)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["name"]
        get_latest_by = "created_at"


class ProjectRepository(TimestampedModel):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="repositories"
    )
    url = models.URLField(help_text="URL of GitHub repository")


class ProjectPitch(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="project_pitches"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="pitches"
    )
    url = models.URLField(help_text="Link to the pitch presentation")

    def __str__(self) -> str:
        return f"{self.semester} {self.project} Pitch: {self.url}"


class ProjectProposal(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="project_proposals"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="proposals"
    )
    url = models.URLField(help_text="Link to the actual proposal document")

    grade = models.DecimalField(
        max_digits=3,
        null=True,
        blank=True,
        decimal_places=1,
        help_text="The grade assigned to this proposal",
    )
    grader = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="graded_project_proposals",
    )
    grader_comments = models.TextField(
        max_length=10000,
        blank=True,
        help_text="Optional comments from the grader",
    )


class ProjectPresentation(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="project_presentations"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="presentations"
    )
    url = models.URLField(help_text="Link to the actual presentation")

    grade = models.DecimalField(
        max_digits=3,
        null=True,
        blank=True,
        decimal_places=1,
        help_text="The grade assigned to this presentation",
    )
    grader = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="graded_project_presentations",
    )
    grader_comments = models.TextField(
        max_length=10000,
        blank=True,
        help_text="Optional comments from the grader",
    )


class Enrollment(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="enrollments"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enrollments",
    )
    credits = models.IntegerField(
        default=0,
        help_text="How many course credits the user is participating in RCOS for this semester. 0 means just for experience.",
    )
    is_for_pay = models.BooleanField(
        "for pay?",
        default=False,
        help_text="Whether the user is participating in RCOS for pay instead of course credit",
    )
    is_project_lead = models.BooleanField("project lead?", default=False)
    is_coordinator = models.BooleanField("coordinator?", default=False)
    is_faculty_advisor = models.BooleanField("faculty advisor?", default=False)

    final_grade = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        help_text="The user's final grade for this semester (if taken for credits)",
        null=True,
        blank=True,
    )

    notes_markdown = models.TextField(
        max_length=10000,
        blank=True,
        help_text="Private notes for admins about this user for this semester",
    )

    def get_absolute_url(self):
        return (
            reverse("users_detail", args=[str(self.user.id)])
            + "?semester="
            + self.semester.id
        )

    def __str__(self) -> str:
        return f"{self.semester.name} - {self.user} - {self.project or 'No project'}"

    class Meta:
        unique_together = ("semester", "user")
        ordering = ["semester"]
        get_latest_by = ["semester"]


class Meeting(TimestampedModel):
    SMALL_GROUP = "small_group"
    LARGE_GROUP = "large_group"
    WORKSHOP = "workshop"
    MENTOR = "mentor"
    COORDINATOR = "coordinator"
    TYPE_CHOICES = (
        (SMALL_GROUP, "Small Group"),
        (LARGE_GROUP, "Large Group"),
        (WORKSHOP, "Workshop"),
        (MENTOR, "Mentor"),
        (COORDINATOR, "Coordinator"),
    )
    TYPE_COLORS = {
        SMALL_GROUP: "red",
        LARGE_GROUP: "blue",
        WORKSHOP: "gold",
        MENTOR: "purple",
        COORDINATOR: "orange",
    }

    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="meetings"
    )
    name = models.CharField(
        max_length=100, blank=True, help_text="The optional title of the meeting"
    )
    host = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Optional host for the meeting (e.g. mentor hosting a workshop",
    )
    type = models.CharField(choices=TYPE_CHOICES, max_length=100)
    is_published = models.BooleanField(
        "published?", default=False, help_text="Whether the meeting is visible to users"
    )
    starts_at = models.DateTimeField(help_text="When the meeting starts")
    ends_at = models.DateTimeField(help_text="When the meeting ends")
    location = models.CharField(
        max_length=500,
        blank=True,
        help_text="Where the meeting takes place either physically or virtually",
    )
    description_markdown = models.TextField(
        max_length=10000,
        blank=True,
        help_text="Optional publicly displayed description for the meeting. Supports Markdown.",
    )

    presentation_url = models.URLField(
        blank=True,
        help_text="The URL to the meeting's slideshow presentation if exists",
    )

    discord_event_id = models.CharField(
        blank=True, max_length=len("759071349561491526") + 5
    )

    # Relationships
    attendances = models.ManyToManyField(
        User, through="MeetingAttendance", related_name="meeting_attendances"
    )

    @property
    def presentation_embed_url(self):
        # https://docs.google.com/presentation/d/1McqgFPrXd3efJty39ekgZpj2kVwapkY6iuU6zGFKuEA/edit#slide=id.g550345e1c6_0_74
        if (
            self.presentation_url
            and "docs.google.com/presentation/d" in self.presentation_url
        ):
            match = re.search(r"[-\w]{25,}", self.presentation_url)
            if match:
                presentation_id = match.group()
                return f"https://docs.google.com/presentation/d/{presentation_id}/embed"
        return None

    @property
    def display_name(self):
        return self.name or self.get_type_display()

    @property
    def color(self):
        return (
            Meeting.TYPE_COLORS[self.type]
            if self.type in Meeting.TYPE_COLORS
            else "grey"
        )

    def get_absolute_url(self):
        return reverse("meetings_detail", args=[str(self.id)])

    def sync_discord_event(self):
        description = f"""**{self.get_type_display()} Meeting**
        
        View details: https://rcos.up.railway.app/meetings/{self.pk}
        {f'Slides: {self.presentation_url}' if self.presentation_url else ''}
        """
        if not self.discord_event_id and self.is_published:
            event = discord.create_server_event(
                name=self.display_name,
                scheduled_start_time=self.starts_at.isoformat(),
                scheduled_end_time=self.ends_at.isoformat(),
                description=description,
                location=self.location,
            )
            self.discord_event_id = event["id"]
            self.save()
        elif self.discord_event_id and self.is_published:
            discord.update_server_event(
                self.discord_event_id,
                name=self.display_name,
                scheduled_start_time=self.starts_at.isoformat(),
                scheduled_end_time=self.ends_at.isoformat(),
                description=description,
                location=self.location,
            )

    def __str__(self) -> str:
        return f"{self.display_name} - {self.starts_at.strftime('%a %b %-d %Y @ %-I:%M %p')}"

    @classmethod
    def get_next(cls):
        today = timezone.datetime.today()
        this_morning = timezone.datetime.combine(
            today, timezone.datetime.min.time(), tzinfo=today.tzinfo
        )
        return cls.objects.filter(
            is_published=True, starts_at__gte=this_morning
        ).first()

    class Meta:
        ordering = ["starts_at"]
        get_latest_by = ["starts_at"]


def sync_meeting_with_discord_event_on_save(
    sender, instance: Meeting, created, *args, **kwargs
):
    # Check if Discord event exists
    instance.sync_discord_event()


post_save.connect(sync_meeting_with_discord_event_on_save, sender=Meeting)


class MeetingAttendance(TimestampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    is_added_by_admin = models.BooleanField(
        default=False,
        help_text="Whether this attendance was added by an admin instead of by the user",
    )


class SmallGroup(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="small_groups"
    )
    name = models.CharField(
        max_length=100, blank=True, help_text="Public-facing name of the Small Group"
    )
    location = models.CharField(
        max_length=200,
        help_text="The location the Small Group meets for Small Group meetings",
    )
    discord_category_id = models.CharField(max_length=200, blank=True)
    discord_role_id = models.CharField(max_length=200, blank=True)

    projects = models.ManyToManyField(Project, related_name="small_groups")
    mentors = models.ManyToManyField(User, related_name="mentored_small_groups")

    @property
    def display_name(self):
        return self.name or self.location or "Unnamed Small Group"

    def get_absolute_url(self):
        return reverse("small_groups_detail", args=[str(self.id)])

    def __str__(self) -> str:
        return self.display_name

    class Meta:
        ordering = ["semester", "name", "location"]


class StatusUpdate(TimestampedModel):
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="status_updates"
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional title to display on Status Update page",
    )
    opens_at = models.DateTimeField(
        help_text="The date and time the status update opens for submissions"
    )
    closes_at = models.DateTimeField(
        help_text="The date and time the status update stops accepting submissions"
    )

    @property
    def display_name(self):
        return (self.name or "Status Update") + " " + self.opens_at.strftime("%x")

    def __str__(self) -> str:
        return self.display_name

    class Meta:
        ordering = ("semester", "opens_at")
        get_latest_by = "opens_at"


class StatusUpdateSubmission(TimestampedModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="status_update_submissions"
    )
    status_update = models.ForeignKey(
        StatusUpdate, on_delete=models.CASCADE, related_name="submissions"
    )

    previous_week = models.TextField(max_length=10000)
    next_week = models.TextField(max_length=10000)
    blockers = models.TextField(max_length=10000)

    grade = models.DecimalField(
        max_digits=1,
        null=True,
        blank=True,
        decimal_places=1,
        help_text="The grade assigned to this submission",
    )
    grader = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="graded_status_update_submissions",
    )
    grader_comments = models.TextField(
        max_length=10000,
        blank=True,
        help_text="Optional comments from the grader to the submitter",
    )

    def __str__(self) -> str:
        return f"{self.user.display_name} submission for {self.status_update}"

    class Meta:
        ordering = ["created_at"]
