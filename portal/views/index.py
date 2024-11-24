from django.core.cache import cache
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.generic.base import TemplateView

from portal.checks import CheckUserCanCreateProject, CheckUserCanEnroll, CheckUserRPI
from portal.forms import SubmitAttendanceForm
from portal.models import Enrollment, Meeting, Project


class IndexView(TemplateView):
    """Renders either the splash page or the user dashboard."""

    template_name = "portal/index/index.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)

        active_semester = cache.get("active_semester")
        data["next_meeting"] = (
            Meeting.get_user_queryset(self.request.user)
            .filter(ends_at__gte=timezone.now())
            .first()
        )

        if self.request.user.is_authenticated:
            data["now"] = timezone.now()
            data["ongoing_meeting"] = Meeting.get_ongoing(self.request.user)
            data["is_user_rpi_check"] = CheckUserRPI().check(self.request.user, None)
            data["can_enroll_check"] = CheckUserCanEnroll().check(
                self.request.user, active_semester
            )
            data["can_create_project_check"] = CheckUserCanCreateProject().check(
                self.request.user, active_semester
            )

            data["enrollment"] = (
                self.request.user.enrollments.filter(semester=active_semester).first()
                if active_semester is not None
                else None
            )
            data["project_team_enrollments"] = (
                data["enrollment"]
                .project.enrollments.filter(semester=active_semester)
                .select_related("user")
                .order_by('-is_project_lead', '-credits', '-user__first_name')
                if data["enrollment"] and data["enrollment"].project
                else []
            )
        else:
            data["submit_attendance_form"] = SubmitAttendanceForm()

            # Fetch and cache ongoing meeting and stats for the splash page
            # We cache it since it doesn't matter if it's slightly outdated
            data["ongoing_meeting"] = None
            data["enrollment_count"] = cache.get_or_set(
                "enrollment_count", Enrollment.objects.count(), 60 * 60 * 24
            )
            data["project_count"] = cache.get_or_set(
                "project_count", Project.objects.count(), 60 * 60 * 24
            )
            data["active_semester_admins"] = cache.get_or_set(
                "active_semester_admins",
                Enrollment.objects.filter(
                    Q(is_faculty_advisor=True) | Q(is_coordinator=True),
                    semester=active_semester,
                ).select_related("user"),
                60 * 60 * 24,
            )

        return data


def handbook(request: HttpRequest) -> HttpResponse:
    """Renders an embedded iframe of the RCOS Handbook,
    optionally with a initial route to display.
    """
    return TemplateResponse(
        request,
        "portal/index/handbook.html",
        {"initial_route": request.GET.get("initial_route")},
    )
