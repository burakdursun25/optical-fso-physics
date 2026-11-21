"""
Paraxial optics for FSO (free-space optics) lens systems.

Physically-defined replacement for the heuristic lens lookup table: lens
systems are built from ray-transfer (ABCD) matrices, composed in order, and
Gaussian beams are propagated through them with the complex q-parameter.

Conventions
-----------
* Ray vector is (y, theta): height [m] and paraxial angle [rad].
* Matrix M = [[A, B], [C, D]] maps (y, theta)_in -> (y, theta)_out.
* System of elements 1..n traversed in order:  M = Mn @ ... @ M2 @ M1.
* Surface radius R > 0 when the centre of curvature lies to the right of
  the surface (light travels left -> right).  R = math.inf is a flat surface.
* Mirrors are handled in the *unfolded* geometry: a concave mirror has
  R > 0 and f = R/2; a convex mirror has R < 0 and f < 0.
* Gaussian beams: q = z + i*zR with zR = pi*w0^2/(M2*lambda) and
  1/q = 1/R(z) - i*M2*lambda/(pi*w(z)^2).  Propagation: q' = (Aq+B)/(Cq+D).
  Beam radii derived from q assume propagation in air (n = 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

__all__ = [
    "free_space", "thin_lens", "thick_lens", "refraction_interface",
    "curved_mirror", "OpticalElement", "OpticalSystem", "RayState",
    "thin_lens_focal_length", "thick_lens_focal_length",
    "rayleigh_range", "beam_radius", "radius_of_curvature",
    "far_field_divergence", "q_from_waist", "q_beam_radius",
    "q_radius_of_curvature", "propagate_q", "beam_through_system",
    "fresnel_reflectance", "surface_transmittance", "lens_transmittance",
    "keplerian_expander", "galilean_expander", "collimator",
    "focusing_receiver", "diverging_lens", "cassegrain_telescope",
    "cassegrain_expander", "LensPreset", "FSO_LENS_PRESETS", "build_preset",
    "received_power_fraction", "geometric_loss_dB", "lens_link_gain_dB",
    "lens_link_budget", "thin_lens_longitudinal_spherical_aberration",
    "airy_radius",
]

# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def _finite_nonzero(value: float, name: str) -> float:
    value = float(value)
    if math.isnan(value) or value == 0.0:
        raise ValueError(f"{name} must be non-zero and not NaN (got {value})")
    return value


def _positive(value: float, name: str, allow_inf: bool = False) -> float:
    value = float(value)
    if math.isnan(value) or value <= 0.0 or (math.isinf(value) and not allow_inf):
        raise ValueError(f"{name} must be positive and finite (got {value})")
    return value


def _non_negative(value: float, name: str) -> float:
    value = float(value)
    if math.isnan(value) or value < 0.0 or math.isinf(value):
        raise ValueError(f"{name} must be >= 0 and finite (got {value})")
    return value


def _radius(value: float, name: str) -> float:
    """Radius of curvature: non-zero, +-inf allowed (flat)."""
    value = float(value)
    if math.isnan(value) or value == 0.0:
        raise ValueError(f"{name} must be non-zero (use math.inf for flat), got {value}")
    return value


# --------------------------------------------------------------------------
# ABCD matrices
# --------------------------------------------------------------------------


def _mat(a: float, b: float, c: float, d: float) -> np.ndarray:
    return np.array([[a, b], [c, d]], dtype=float)


def free_space(d: float) -> np.ndarray:
    """Propagation through distance d [m] in a homogeneous medium."""
    d = _non_negative(d, "distance d")
    return _mat(1.0, d, 0.0, 1.0)


def thin_lens(f: float) -> np.ndarray:
    """Thin lens of focal length f [m] (f < 0: diverging)."""
    f = _finite_nonzero(f, "focal length f")
    return _mat(1.0, 0.0, -1.0 / f, 1.0)


def refraction_interface(n1: float, n2: float, R: float = math.inf) -> np.ndarray:
    """Refraction at a (curved) interface from index n1 into n2.

    det = n1/n2.  R = inf gives a flat interface.
    """
    n1 = _positive(n1, "n1")
    n2 = _positive(n2, "n2")
    R = _radius(R, "radius R")
    return _mat(1.0, 0.0, (n1 - n2) / (R * n2), n1 / n2)


def curved_mirror(R: float) -> np.ndarray:
    """Spherical mirror (unfolded geometry), f = R/2.  R = inf: flat mirror."""
    R = _radius(R, "mirror radius R")
    return _mat(1.0, 0.0, -2.0 / R, 1.0)


def thick_lens(n: float, R1: float, R2: float, d: float, n_surround: float = 1.0) -> np.ndarray:
    """Thick lens: refraction(R1) -> glass thickness d -> refraction(R2)."""
    n = _positive(n, "lens index n")
    d = _non_negative(d, "thickness d")
    n_s = _positive(n_surround, "n_surround")
    m1 = refraction_interface(n_s, n, R1)
    m2 = free_space(d)
    m3 = refraction_interface(n, n_s, R2)
    return m3 @ m2 @ m1


# --------------------------------------------------------------------------
# Lensmaker
# --------------------------------------------------------------------------


def thin_lens_focal_length(n: float, R1: float, R2: float) -> float:
    """Thin-lens lensmaker: 1/f = (n-1)(1/R1 - 1/R2). Returns +-inf if power = 0."""
    n = _positive(n, "lens index n")
    R1 = _radius(R1, "R1")
    R2 = _radius(R2, "R2")
    power = (n - 1.0) * (1.0 / R1 - 1.0 / R2)
    return math.inf if power == 0.0 else 1.0 / power


def thick_lens_focal_length(n: float, R1: float, R2: float, d: float) -> float:
    """Thick-lens lensmaker (in air):
    1/f = (n-1) [1/R1 - 1/R2 + (n-1) d / (n R1 R2)]."""
    n = _positive(n, "lens index n")
    R1 = _radius(R1, "R1")
    R2 = _radius(R2, "R2")
    d = _non_negative(d, "thickness d")
    power = (n - 1.0) * (1.0 / R1 - 1.0 / R2 + (n - 1.0) * d / (n * R1 * R2))
    return math.inf if power == 0.0 else 1.0 / power


# --------------------------------------------------------------------------
# Elements and systems
# --------------------------------------------------------------------------


@dataclass
class OpticalElement:
    """One element of an optical train.

    name            display name
    matrix          2x2 ABCD matrix
    aperture_radius clear semi-aperture [m] (inf = unlimited)
    length          physical axial length [m] consumed by the element
    transmittance   power transmittance (Fresnel / coating / mirror reflectivity)
    obstruction_radius  central obstruction radius [m] (e.g. Cassegrain secondary)
    """
    name: str
    matrix: np.ndarray
    aperture_radius: float = math.inf
    length: float = 0.0
    transmittance: float = 1.0
    obstruction_radius: float = 0.0

    def __post_init__(self):
        self.matrix = np.asarray(self.matrix, dtype=float)
        if self.matrix.shape != (2, 2):
            raise ValueError("matrix must be 2x2")
        self.aperture_radius = _positive(self.aperture_radius, "aperture_radius", allow_inf=True)
        self.length = _non_negative(self.length, "length")
        if not (0.0 <= self.transmittance <= 1.0):
            raise ValueError("transmittance must be within [0, 1]")
        self.obstruction_radius = _non_negative(self.obstruction_radius, "obstruction_radius")
        if self.obstruction_radius >= self.aperture_radius:
            raise ValueError("obstruction_radius must be smaller than aperture_radius")


@dataclass
class RayState:
    """Paraxial ray at one element: height/angle on entry and exit."""
    name: str
    y_in: float
    theta_in: float
    y_out: float
    theta_out: float
    aperture_radius: float
    clipped: bool


class OpticalSystem:
    """Ordered train of optical elements (first element = first met by light)."""

    def __init__(self, elements: Optional[Sequence[OpticalElement]] = None,
                 n_in: float = 1.0, n_out: float = 1.0):
        self.elements: List[OpticalElement] = list(elements or [])
        self.n_in = _positive(n_in, "n_in")
        self.n_out = _positive(n_out, "n_out")

    def add(self, element: OpticalElement) -> "OpticalSystem":
        self.elements.append(element)
        return self

    @property
    def matrix(self) -> np.ndarray:
        """System matrix M = Mn ... M1."""
        m = np.eye(2)
        for el in self.elements:
            m = el.matrix @ m
        return m

    @property
    def total_length(self) -> float:
        return sum(el.length for el in self.elements)

    @property
    def transmittance(self) -> float:
        t = 1.0
        for el in self.elements:
            t *= el.transmittance
        return t

    @property
    def determinant(self) -> float:
        return float(np.linalg.det(self.matrix))

    def determinant_ok(self, tol: float = 1e-9) -> bool:
        """det(M) must equal n_in / n_out."""
        return abs(self.determinant - self.n_in / self.n_out) <= tol

    @property
    def power(self) -> float:
        """Optical power -C [1/m]."""
        return -float(self.matrix[1, 0])

    @property
    def is_afocal(self) -> bool:
        m = self.matrix
        return abs(m[1, 0]) <= 1e-12 * max(1.0, abs(m[0, 0]), abs(m[1, 1]))

    @property
    def efl(self) -> float:
        """Effective focal length -1/C (inf for afocal systems)."""
        return math.inf if self.is_afocal else -1.0 / float(self.matrix[1, 0])

    @property
    def bfl(self) -> float:
        """Back focal length: output plane -> rear focal point, -A/C."""
        if self.is_afocal:
            return math.inf
        m = self.matrix
        return -float(m[0, 0] / m[1, 0])

    @property
    def ffl(self) -> float:
        """Front focal length: front focal point -> input plane, -D/C
        (positive when the focal point lies before the first surface)."""
        if self.is_afocal:
            return math.inf
        m = self.matrix
        return -float(m[1, 1] / m[1, 0])

    def principal_planes(self) -> Optional[Tuple[float, float]]:
        """(P, P'): front principal plane measured from the input plane and
        rear principal plane measured from the output plane (positive = to the
        right).  None for afocal systems."""
        if self.is_afocal:
            return None
        m = self.matrix
        c = float(m[1, 0])
        return (float(m[1, 1]) - 1.0) / c, (1.0 - float(m[0, 0])) / c

    @property
    def angular_magnification(self) -> float:
        """D of the system matrix; for an afocal expander this is 1/(linear
        magnification) (e.g. -1/M for a Keplerian telescope)."""
        return float(self.matrix[1, 1])

    @property
    def linear_magnification(self) -> float:
        """A of the system matrix (beam-size magnification of an afocal system)."""
        return float(self.matrix[0, 0])

    def trace_ray(self, y0: float, theta0: float = 0.0) -> List[RayState]:
        """Trace a paraxial ray element by element."""
        ray = np.array([float(y0), float(theta0)])
        out: List[RayState] = []
        for el in self.elements:
            y_in, th_in = float(ray[0]), float(ray[1])
            ray = el.matrix @ ray
            # the ray height at a thin element equals its entry height
            clipped = abs(y_in) > el.aperture_radius or (
                el.obstruction_radius > 0.0 and abs(y_in) < el.obstruction_radius)
            out.append(RayState(el.name, y_in, th_in, float(ray[0]), float(ray[1]),
                                el.aperture_radius, bool(clipped)))
        return out

    def vignetted(self, y0: float, theta0: float = 0.0) -> bool:
        return any(s.clipped for s in self.trace_ray(y0, theta0))


# --------------------------------------------------------------------------
# Gaussian beam optics
# --------------------------------------------------------------------------


def rayleigh_range(w0: float, wavelength: float, m2: float = 1.0) -> float:
    """zR = pi w0^2 / (M^2 lambda)."""
    w0 = _positive(w0, "w0")
    wavelength = _positive(wavelength, "wavelength")
    m2 = _positive(m2, "M^2")
    return math.pi * w0 * w0 / (m2 * wavelength)


def beam_radius(z: float, w0: float, wavelength: float, m2: float = 1.0) -> float:
    """w(z) = w0 sqrt(1 + (z/zR)^2), z measured from the waist."""
    zr = rayleigh_range(w0, wavelength, m2)
    return w0 * math.sqrt(1.0 + (float(z) / zr) ** 2)


def radius_of_curvature(z: float, w0: float, wavelength: float, m2: float = 1.0) -> float:
    """R(z) = z (1 + (zR/z)^2); inf at the waist."""
    zr = rayleigh_range(w0, wavelength, m2)
    z = float(z)
    if z == 0.0:
        return math.inf
    return z * (1.0 + (zr / z) ** 2)


def far_field_divergence(w0: float, wavelength: float, m2: float = 1.0) -> float:
    """Half-angle divergence theta = M^2 lambda / (pi w0) [rad]."""
    w0 = _positive(w0, "w0")
    wavelength = _positive(wavelength, "wavelength")
    m2 = _positive(m2, "M^2")
    return m2 * wavelength / (math.pi * w0)


def q_from_waist(w0: float, wavelength: float, m2: float = 1.0, z: float = 0.0) -> complex:
    """q-parameter at distance z from the waist: q = z + i zR."""
    return complex(float(z), rayleigh_range(w0, wavelength, m2))


def _inv_q(q: complex) -> complex:
    return 1.0 / q


def q_beam_radius(q: complex, wavelength: float, m2: float = 1.0) -> float:
    """Beam radius from q:  w = sqrt(-M^2 lambda / (pi Im(1/q)))."""
    wavelength = _positive(wavelength, "wavelength")
    m2 = _positive(m2, "M^2")
    im = _inv_q(q).imag
    if im >= 0.0:
        raise ValueError("q does not describe a physical (confined) Gaussian beam")
    return math.sqrt(-m2 * wavelength / (math.pi * im))


def q_radius_of_curvature(q: complex) -> float:
    """Wavefront radius of curvature 1/Re(1/q) (inf for a plane wavefront)."""
    re = _inv_q(q).real
    return math.inf if re == 0.0 else 1.0 / re


def propagate_q(q: complex, matrix: np.ndarray) -> complex:
    """q' = (A q + B) / (C q + D)."""
    m = np.asarray(matrix, dtype=float)
    return (m[0, 0] * q + m[0, 1]) / (m[1, 0] * q + m[1, 1])


def beam_through_system(w0: float, wavelength: float, system: OpticalSystem,
                        z_after: float = 0.0, m2: float = 1.0,
                        z_before: float = 0.0) -> Dict[str, object]:
    """Propagate a Gaussian beam through `system`.

    The input waist (radius w0) lies z_before [m] ahead of the first element;
    z_after is the distance behind the system's output plane at which the beam
    is evaluated.  Returned dict:

      w, R           beam radius / wavefront radius at z_after
      divergence     far-field half-angle of the output beam (M^2 lambda/(pi w0'))
      w0_out         output waist radius, z_waist_out its position relative to
                     the system output plane (positive = behind it)
      zR_out, q      output Rayleigh range and q at z_after
      element_radii  [(name, w at element entry), ...]
      clipping_transmission  power passing every aperture / obstruction
      transmittance  product of element transmittances (Fresnel/coatings)
      total_transmission     clipping * transmittance
    """
    w0 = _positive(w0, "w0")
    wavelength = _positive(wavelength, "wavelength")
    m2 = _positive(m2, "M^2")
    z_after = _non_negative(z_after, "z_after")
    z_before = _non_negative(z_before, "z_before")

    q = q_from_waist(w0, wavelength, m2, z_before)
    radii = []
    clip = 1.0
    for el in system.elements:
        w_el = q_beam_radius(q, wavelength, m2)
        radii.append((el.name, w_el))
        if math.isfinite(el.aperture_radius) or el.obstruction_radius > 0.0:
            pass_outer = 1.0 - math.exp(-2.0 * el.aperture_radius ** 2 / w_el ** 2) \
                if math.isfinite(el.aperture_radius) else 1.0
            blocked = 1.0 - math.exp(-2.0 * el.obstruction_radius ** 2 / w_el ** 2)
            clip *= max(0.0, pass_outer - blocked)
        q = propagate_q(q, el.matrix)

    q_out = q  # at output plane
    zr_out = q_out.imag
    w0_out = math.sqrt(zr_out * m2 * wavelength / math.pi)
    q_end = q_out + z_after
    t = system.transmittance
    return {
        "w": q_beam_radius(q_end, wavelength, m2),
        "R": q_radius_of_curvature(q_end),
        "divergence": m2 * wavelength / (math.pi * w0_out),
        "w0_out": w0_out,
        "z_waist_out": -q_out.real,
        "zR_out": zr_out,
        "q": q_end,
        "element_radii": radii,
        "clipping_transmission": clip,
        "transmittance": t,
        "total_transmission": clip * t,
    }


# --------------------------------------------------------------------------
# Surface transmittance
# --------------------------------------------------------------------------


def fresnel_reflectance(n1: float, n2: float) -> float:
    """Normal-incidence Fresnel power reflectance ((n1-n2)/(n1+n2))^2."""
    n1 = _positive(n1, "n1")
    n2 = _positive(n2, "n2")
    return ((n1 - n2) / (n1 + n2)) ** 2


def surface_transmittance(n1: float, n2: float, ar_reflectance: Optional[float] = None) -> float:
    """1 - R for one surface; ar_reflectance replaces the bare Fresnel R."""
    if ar_reflectance is None:
        r = fresnel_reflectance(n1, n2)
    else:
        r = float(ar_reflectance)
        if not (0.0 <= r < 1.0):
            raise ValueError("ar_reflectance must be within [0, 1)")
    return 1.0 - r


def lens_transmittance(n: float, surfaces: int = 2, n_surround: float = 1.0,
                       ar_reflectance: Optional[float] = None) -> float:
    """(1 - R)^surfaces (absorption neglected)."""
    if int(surfaces) != surfaces or surfaces < 0:
        raise ValueError("surfaces must be a non-negative integer")
    return surface_transmittance(n_surround, n, ar_reflectance) ** int(surfaces)


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------


def _lens_el(name, f, aperture_radius, n, ar):
    return OpticalElement(name, thin_lens(f), aperture_radius,
                          transmittance=lens_transmittance(n, 2, 1.0, ar))


def _afocal_pair(name1, f1, a1, name2, f2, a2, n, ar):
    f1 = _finite_nonzero(f1, "f1")
    f2 = _finite_nonzero(f2, "f2")
    d = f1 + f2
    if d < 0:
        raise ValueError("f1 + f2 must be >= 0 for a physical afocal pair")
    return OpticalSystem([
        _lens_el(name1, f1, a1, n, ar),
        OpticalElement("Boşluk", free_space(d), length=d),
        _lens_el(name2, f2, a2, n, ar),
    ])


def keplerian_expander(f1: float = 0.05, f2: float = 0.25, a1: float = 0.0125,
                       a2: float = 0.0375, n: float = 1.5007,
                       ar_reflectance: Optional[float] = 0.005) -> OpticalSystem:
    """Keplerian expander: two positive lenses, spacing f1+f2, M = f2/f1
    (inverts the beam; internal focus)."""
    if f1 <= 0 or f2 <= 0:
        raise ValueError("Keplerian expander needs f1 > 0 and f2 > 0")
    return _afocal_pair("Lens 1", f1, a1, "Lens 2", f2, a2, n, ar_reflectance)


def galilean_expander(f1: float = -0.05, f2: float = 0.25, a1: float = 0.0125,
                      a2: float = 0.0375, n: float = 1.5007,
                      ar_reflectance: Optional[float] = 0.005) -> OpticalSystem:
    """Galilean expander: negative then positive lens, spacing f1+f2,
    M = f2/|f1| (upright, no internal focus)."""
    if f1 >= 0 or f2 <= 0:
        raise ValueError("Galilean expander needs f1 < 0 and f2 > 0")
    return _afocal_pair("Negatif lens", f1, a1, "Pozitif lens", f2, a2, n, ar_reflectance)


def collimator(f: float = 0.05, aperture_radius: float = 0.0125, n: float = 1.5007,
               ar_reflectance: Optional[float] = 0.005) -> OpticalSystem:
    """Single-lens collimator; the source waist must sit at the front focus
    (z_before = f in beam_through_system)."""
    if f <= 0:
        raise ValueError("collimator focal length must be positive")
    return OpticalSystem([_lens_el("Kollimatör lens", f, aperture_radius, n, ar_reflectance)])


def focusing_receiver(f: float = 0.2, aperture_radius: float = 0.05, n: float = 1.5007,
                      ar_reflectance: Optional[float] = 0.005) -> OpticalSystem:
    """Receiver collecting lens focusing power onto a detector at the focus."""
    if f <= 0:
        raise ValueError("receiver lens focal length must be positive")
    return OpticalSystem([_lens_el("Alıcı lens", f, aperture_radius, n, ar_reflectance)])


def diverging_lens(f: float = -0.08, aperture_radius: float = 0.025, n: float = 1.5007,
                   ar_reflectance: Optional[float] = 0.005) -> OpticalSystem:
    """Single negative lens (degrades the link)."""
    if f >= 0:
        raise ValueError("diverging lens focal length must be negative")
    return OpticalSystem([_lens_el("Iraksak lens", f, aperture_radius, n, ar_reflectance)])


def cassegrain_telescope(f_primary: float = 0.4, f_secondary: float = -0.1,
                         separation: float = 0.32, primary_radius: float = 0.1,
                         secondary_radius: float = 0.02,
                         mirror_reflectivity: float = 0.98) -> OpticalSystem:
    """Focusing Cassegrain (unfolded): concave primary, convex secondary
    (f_secondary < 0), separation d.  1/f = 1/f1 + 1/f2 - d/(f1 f2), so the
    EFL is f1 f2 / (f1 + f2 - d) (positive, converging, for d > f1 + f2)."""
    f1 = _positive(f_primary, "f_primary")
    f2 = _finite_nonzero(f_secondary, "f_secondary")
    if f2 >= 0:
        raise ValueError("Cassegrain secondary must be convex (f_secondary < 0)")
    d = _positive(separation, "separation")
    if not (0.0 < mirror_reflectivity <= 1.0):
        raise ValueError("mirror_reflectivity must be within (0, 1]")
    return OpticalSystem([
        OpticalElement("Birincil ayna", curved_mirror(2 * f1), primary_radius,
                       transmittance=mirror_reflectivity, obstruction_radius=secondary_radius),
        OpticalElement("Boşluk", free_space(d), length=d),
        OpticalElement("İkincil ayna", curved_mirror(2 * f2), secondary_radius,
                       transmittance=mirror_reflectivity),
    ])


def cassegrain_expander(f_primary: float = 0.6, f_secondary: float = -0.06,
                        primary_radius: float = 0.1, secondary_radius: float = 0.02,
                        mirror_reflectivity: float = 0.98) -> OpticalSystem:
    """Afocal Cassegrain-type beam expander (transmit telescope): the small
    beam meets the convex secondary first, then the concave primary;
    separation f1 + f2 (f2 < 0), M = f1/|f2|.  The secondary obstructs the
    centre of the expanded beam, so the input beam should be large enough
    that the expanded Gaussian is not mostly blocked by the secondary
    (preset default w0 = 6 mm, M = 10, w at primary ~ 6 cm)."""
    f1 = _positive(f_primary, "f_primary")
    f2 = _finite_nonzero(f_secondary, "f_secondary")
    if f2 >= 0 or f1 + f2 <= 0:
        raise ValueError("need f_secondary < 0 and f_primary > |f_secondary|")
    if not (0.0 < mirror_reflectivity <= 1.0):
        raise ValueError("mirror_reflectivity must be within (0, 1]")
    d = f1 + f2
    return OpticalSystem([
        OpticalElement("İkincil ayna", curved_mirror(2 * f2), secondary_radius,
                       transmittance=mirror_reflectivity),
        OpticalElement("Boşluk", free_space(d), length=d),
        OpticalElement("Birincil ayna", curved_mirror(2 * f1), primary_radius,
                       transmittance=mirror_reflectivity, obstruction_radius=secondary_radius),
    ])


@dataclass
class LensPreset:
    """A physically defined lens system plus link-modelling metadata."""
    key: str
    name: str
    description: str
    role: str                      # "none" | "transmit" | "receive"
    system: OpticalSystem
    default_w0: float              # source waist radius [m]
    source_offset_m: float = 0.0   # source waist -> first element [m]
    rx_aperture_radius: Optional[float] = None   # collecting radius if role == "receive"
    params: Dict[str, float] = field(default_factory=dict)

    @property
    def magnification(self) -> float:
        """Beam-size magnification of an afocal system (|A|); 1 for none."""
        if not self.system.elements:
            return 1.0
        return abs(self.system.linear_magnification) if self.system.is_afocal else math.nan


_FIBER_W0 = 5.2e-6   # SMF-28 mode-field radius at 1550 nm
_LASER_W0 = 2.0e-3   # collimated laser output

FSO_LENS_PRESETS: Dict[str, Dict[str, object]] = {
    "none": {
        "name": "Lens Yok", "description": "Lens sistemi yok – ham ışın",
        "role": "none", "builder": None, "default_w0": _LASER_W0, "source_offset_m": 0.0,
        "params": {},
    },
    "collimating": {
        "name": "Kollimatör Lens",
        "description": "Fiber ucu odak noktasında; tek lens ile ışını paralel hale getirir",
        "role": "transmit", "builder": collimator, "default_w0": _FIBER_W0,
        "source_offset_m": 0.05, "params": {"f": 0.05, "aperture_radius": 0.0125},
    },
    "focusing": {
        "name": "Odaklayıcı Alıcı Lens",
        "description": "Alıcı tarafında toplama açıklığını büyütür ve dedektöre odaklar",
        "role": "receive", "builder": focusing_receiver, "default_w0": _LASER_W0,
        "source_offset_m": 0.0, "params": {"f": 0.2, "aperture_radius": 0.05},
    },
    "keplerian_telescope": {
        "name": "Keplerian Teleskop",
        "description": "İki pozitif lens ışın genişletici, M = f2/f1",
        "role": "transmit", "builder": keplerian_expander, "default_w0": _LASER_W0,
        "source_offset_m": 0.0,
        "params": {"f1": 0.05, "f2": 0.25, "a1": 0.0125, "a2": 0.0375},
    },
    "galilean_telescope": {
        "name": "Galilean Teleskop",
        "description": "Negatif + pozitif lens ışın genişletici, M = f2/|f1|",
        "role": "transmit", "builder": galilean_expander, "default_w0": _LASER_W0,
        "source_offset_m": 0.0,
        "params": {"f1": -0.05, "f2": 0.25, "a1": 0.0125, "a2": 0.0375},
    },
    "cassegrain": {
        "name": "Cassegrain Teleskop",
        "description": "Çift aynalı afokal ışın genişletici, büyük açıklık, merkez engelli",
        "role": "transmit", "builder": cassegrain_expander, "default_w0": 6.0e-3,
        "source_offset_m": 0.0,
        "params": {"f_primary": 0.6, "f_secondary": -0.06,
                   "primary_radius": 0.1, "secondary_radius": 0.02},
    },
    "diverging": {
        "name": "Iraksak Lens",
        "description": "Test amaçlı ıraksak lens – performansı düşürür",
        "role": "transmit", "builder": diverging_lens, "default_w0": _LASER_W0,
        "source_offset_m": 0.0, "params": {"f": -0.08, "aperture_radius": 0.025},
    },
}


def build_preset(name: str, **overrides) -> LensPreset:
    """Build a preset by name.  Keyword overrides replace builder parameters
    (e.g. f2=0.5, a2=0.06) or the metadata `default_w0`, `source_offset_m`."""
    if name not in FSO_LENS_PRESETS:
        raise ValueError(f"unknown lens preset '{name}'; available: {sorted(FSO_LENS_PRESETS)}")
    spec = FSO_LENS_PRESETS[name]
    meta_keys = {"default_w0", "source_offset_m"}
    meta = {k: overrides.pop(k) for k in list(overrides) if k in meta_keys}
    params = dict(spec["params"])
    params.update(overrides)
    builder = spec["builder"]
    if builder is None:
        if overrides:
            raise ValueError("preset 'none' takes no parameters")
        system = OpticalSystem([])
    else:
        system = builder(**params)
    role = spec["role"]
    rx = None
    if role == "receive":
        rx = params.get("aperture_radius")
    return LensPreset(
        key=name, name=spec["name"], description=spec["description"], role=role,
        system=system, default_w0=float(meta.get("default_w0", spec["default_w0"])),
        source_offset_m=float(meta.get("source_offset_m", spec["source_offset_m"])),
        rx_aperture_radius=rx, params=params,
    )


# --------------------------------------------------------------------------
# Link-level helpers
# --------------------------------------------------------------------------


def received_power_fraction(w_at_rx: float, rx_radius: float) -> float:
    """Gaussian beam power through a centred circular aperture:
    1 - exp(-2 a^2 / w^2)."""
    w = _positive(w_at_rx, "w_at_rx")
    a = _positive(rx_radius, "rx_radius", allow_inf=True)
    if math.isinf(a):
        return 1.0
    return 1.0 - math.exp(-2.0 * a * a / (w * w))


def geometric_loss_dB(w0: float, wavelength: float, distance_m: float,
                      rx_radius: float, m2: float = 1.0, z_waist_to_start: float = 0.0) -> float:
    """Geometric loss [dB] (>= 0) of a Gaussian beam with waist w0 captured by
    a circular aperture after distance_m (+ z_waist_to_start from the waist)."""
    distance_m = _non_negative(distance_m, "distance_m")
    z = distance_m + _non_negative(z_waist_to_start, "z_waist_to_start")
    w = beam_radius(z, w0, wavelength, m2)
    frac = received_power_fraction(w, rx_radius)
    return -10.0 * math.log10(max(frac, 1e-300))


def _resolve_preset(preset: Union[str, LensPreset], **overrides) -> LensPreset:
    if isinstance(preset, LensPreset):
        if overrides:
            raise ValueError("overrides only apply when preset is given by name")
        return preset
    return build_preset(preset, **overrides)


def lens_link_budget(preset: Union[str, LensPreset], distance_m: float,
                     wavelength: float = 1550e-9, w0: Optional[float] = None,
                     rx_radius: float = 0.025, m2: float = 1.0) -> Dict[str, float]:
    """Compare a link with and without `preset`.

    Transmit presets reshape the beam (clipping and surface losses included);
    the baseline propagates the same source waist over the same physical
    path with no optics.  Receive presets replace the bare receiver radius
    `rx_radius` with the lens aperture (times lens transmittance).
    """
    p = _resolve_preset(preset)
    distance_m = _non_negative(distance_m, "distance_m")
    wavelength = _positive(wavelength, "wavelength")
    rx_radius = _positive(rx_radius, "rx_radius")
    w0 = p.default_w0 if w0 is None else _positive(w0, "w0")

    z_base = p.source_offset_m + p.system.total_length + distance_m
    w_base = beam_radius(z_base, w0, wavelength, m2)

    if p.role == "receive":
        # source beam unchanged, only the collecting aperture differs
        w_with = beam_radius(distance_m, w0, wavelength, m2)
        w_base = w_with
        frac_base = received_power_fraction(w_base, rx_radius)
        frac_with = received_power_fraction(w_with, p.rx_aperture_radius) * p.system.transmittance
        total_t = p.system.transmittance
        div_with = far_field_divergence(w0, wavelength, m2)
    else:
        res = beam_through_system(w0, wavelength, p.system, distance_m, m2, p.source_offset_m)
        w_with = res["w"]
        frac_base = received_power_fraction(w_base, rx_radius)
        frac_with = received_power_fraction(w_with, rx_radius) * res["total_transmission"]
        total_t = res["total_transmission"]
        div_with = res["divergence"]

    frac_base = max(frac_base, 1e-300)
    frac_with = max(frac_with, 1e-300)
    return {
        "w_without_m": w_base, "w_with_m": w_with,
        "fraction_without": frac_base, "fraction_with": frac_with,
        "loss_without_dB": -10.0 * math.log10(frac_base),
        "loss_with_dB": -10.0 * math.log10(frac_with),
        "gain_dB": 10.0 * math.log10(frac_with / frac_base),
        "system_transmission": total_t,
        "divergence_without_rad": far_field_divergence(w0, wavelength, m2),
        "divergence_with_rad": div_with,
    }


def lens_link_gain_dB(preset: Union[str, LensPreset], distance_m: float,
                      wavelength: float = 1550e-9, w0: Optional[float] = None,
                      rx_radius: float = 0.025, m2: float = 1.0) -> float:
    """Net link gain [dB] of the lens system vs. no lens (positive = better)."""
    return lens_link_budget(preset, distance_m, wavelength, w0, rx_radius, m2)["gain_dB"]


# --------------------------------------------------------------------------
# Aberrations / diffraction
# --------------------------------------------------------------------------


def thin_lens_longitudinal_spherical_aberration(f: float, n: float, h: float,
                                                R1: Optional[float] = None,
                                                R2: Optional[float] = None,
                                                shape_factor: Optional[float] = None) -> float:
    """Third-order longitudinal spherical aberration of a thin lens in air for
    an object at infinity (Coddington form), marginal ray height h [m].

        LSA = -(h^2 / (8 n (n-1) f)) [ (n+2)/(n-1) q^2 - 4 (n+1) q
                                        + (3n+2)(n-1) + n^3/(n-1) ]

    with shape factor q = (R2 + R1)/(R2 - R1).  Give either (R1, R2) (f is then
    taken from the lensmaker equation and the `f` argument is ignored) or
    shape_factor with f.  Negative = marginal focus in front of paraxial focus
    (under-corrected positive lens).
    """
    n = _positive(n, "n")
    if n == 1.0:
        raise ValueError("n must differ from 1")
    h = _non_negative(h, "h")
    if (R1 is None) != (R2 is None):
        raise ValueError("give both R1 and R2")
    if R1 is not None:
        R1 = _radius(R1, "R1")
        R2 = _radius(R2, "R2")
        if R1 == R2:
            raise ValueError("R1 and R2 must differ")
        f = thin_lens_focal_length(n, R1, R2)
        if math.isinf(R2) and math.isinf(R1):
            raise ValueError("flat plate has no focal length")
        q = (1.0 if math.isinf(R2) else (-1.0 if math.isinf(R1)
             else (R2 + R1) / (R2 - R1)))
    else:
        if shape_factor is None:
            raise ValueError("give R1/R2 or shape_factor")
        q = float(shape_factor)
        f = _finite_nonzero(f, "f")
    bracket = ((n + 2.0) / (n - 1.0) * q * q - 4.0 * (n + 1.0) * q
               + (3.0 * n + 2.0) * (n - 1.0) + n ** 3 / (n - 1.0))
    return -(h * h) / (8.0 * n * (n - 1.0) * f) * bracket


def airy_radius(wavelength: float, f: float, D: float) -> float:
    """Radius of the first Airy dark ring: 1.22 lambda f / D."""
    wavelength = _positive(wavelength, "wavelength")
    f = _positive(f, "f")
    D = _positive(D, "D")
    return 1.22 * wavelength * f / D
