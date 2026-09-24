import math

import pytest

from backend.engine import Vec3, vectorialSnellLaw, verifySnellLaw


def test_vec3_operations_are_immutable_and_precise():
    vector = Vec3(3.0, 4.0, 0.0)

    assert vector.length() == pytest.approx(5.0)
    assert vector.normalize().length() == pytest.approx(1.0)
    assert vector.add(Vec3(1.0, -1.0, 2.0)).x == pytest.approx(4.0)
    assert vector.cross(Vec3(0.0, 0.0, 1.0)).x == pytest.approx(4.0)
    assert vector.x == pytest.approx(3.0)


def test_snell_law_air_to_glass_at_thirty_degrees():
    theta_i = math.radians(30.0)
    incident = Vec3(math.sin(theta_i), 0.0, -math.cos(theta_i))
    result = vectorialSnellLaw(incident, Vec3(0.0, 0.0, 1.0), 1.0, 1.5)

    assert result is not None
    assert math.sqrt(1.0 - result["cosTheta_t"] ** 2) == pytest.approx(
        math.sin(theta_i) / 1.5
    )
    assert verifySnellLaw(1.0, 1.5, result["cosTheta_i"], result["cosTheta_t"])["valid"]


def test_snell_law_returns_none_for_total_internal_reflection():
    critical = math.asin(1.0 / 1.5)
    incident = Vec3(math.sin(critical + math.radians(5.0)), 0.0, -math.cos(critical + math.radians(5.0)))

    assert vectorialSnellLaw(incident, Vec3(0.0, 0.0, 1.0), 1.5, 1.0) is None


def test_snell_law_preserves_reciprocity():
    incident = Vec3(math.sin(math.radians(10.0)), 0.0, -math.cos(math.radians(10.0)))
    forward = vectorialSnellLaw(incident, Vec3(0.0, 0.0, 1.0), 1.0003, 1.0001)
    backward = vectorialSnellLaw(forward["direction"].scale(-1.0), Vec3(0.0, 0.0, -1.0), 1.0001, 1.0003)

    recovered = backward["direction"].scale(-1.0)
    assert recovered.x == pytest.approx(incident.x, abs=1e-10)
    assert recovered.z == pytest.approx(incident.z, abs=1e-10)


def test_snell_law_preserves_tangential_component():
    incident = Vec3(math.sin(math.radians(20.0)), 0.0, -math.cos(math.radians(20.0)))
    normal = Vec3(0.0, 0.0, 1.0)
    result = vectorialSnellLaw(incident, normal, 1.33, 1.0)

    incident_tangent = incident.sub(normal.scale(incident.dot(normal)))
    transmitted_tangent = result["direction"].sub(normal.scale(result["direction"].dot(normal)))
    assert 1.33 * incident_tangent.length() == pytest.approx(1.0 * transmitted_tangent.length(), abs=1e-10)
