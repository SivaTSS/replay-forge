"""Registered application compatibility checks before opening a replay session."""

from replayforge.applications.models import SurfaceLaunch
from replayforge.applications.registry import ApplicationRegistry
from replayforge.capabilities.models import CapabilityArtifact, RenderedTextCondition
from replayforge.surfaces.models import SurfaceError
from replayforge.surfaces.ports import SurfaceSession


def validate_application_compatibility(
    registry: ApplicationRegistry, artifact: CapabilityArtifact, tenant: str
) -> None:
    try:
        application = registry.get(artifact.capability.application_family)
        launch = application.resolve(tenant, artifact.compatibility.entry_point)
    except ValueError as error:
        raise SurfaceError(
            "application_not_registered", "The capability target is not registered."
        ) from error
    compatibility = artifact.compatibility
    if (
        artifact.capability.surface != launch.surface
        or compatibility.surface_contract != launch.surface_contract
        or compatibility.base_variant != application.base_variant
        or compatibility.rendered_surface != launch.rendered_surface
    ):
        raise SurfaceError(
            "application_contract_mismatch",
            "The registered application no longer matches the capability's surface contract.",
        )
    if not artifact.policy.allowed_entry_points <= application.entry_points.keys():
        raise SurfaceError(
            "entry_point_contract_mismatch",
            "A capability navigation entry point is no longer registered.",
        )


def validate_rendered_readiness(session: SurfaceSession, launch: SurfaceLaunch) -> None:
    """Registration declares shared invariants; discovery fingerprints are descriptive."""
    for landmark, required in (
        *((item, True) for item in launch.required_landmarks),
        *((item, False) for item in launch.forbidden_landmarks),
    ):
        condition = RenderedTextCondition(kind="rendered_text", value=landmark.value)
        present = (
            session.wait_until(condition, {}, {}, 10_000)
            if required
            else session.evaluate(condition, {}, {})
        )
        if present != required:
            raise SurfaceError(
                "compatibility_landmark_mismatch",
                "The entry surface does not satisfy its registered readiness contract.",
            )
