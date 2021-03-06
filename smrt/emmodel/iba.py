# coding: utf-8

"""Compute scattering from Improved Born Approximation theory as described in Mätzler 1998 and Mätzler and Wiesman 1999, except the
absorption coefficient which is computed with Polden von Staten formulation instead of the Eq 24 in Mätzler 1998. See iba_original.py for
a fully conforming IBA version.
 This model allows for different microstructural models provided that the Fourier transform of the correlation function
may be performed. All properties relate to a single layer.

"""

# Stdlib import

# other import
import numpy as np
import scipy.integrate
import scipy.fftpack

# local import
from ..core.error import SMRTError
from ..core.globalconstants import C_SPEED
from .effective_permittivity import depolarization_factors, polder_van_santen
from ..core.lib import smrt_matrix, generic_ft_even_matrix, len_atleast_1d

#
# For developers: all emmodel must implement the `effective_permittivity`, `ke` and `phase` functions with the same arguments as here
# initialisation and precomputation can be done in the prepare method that is called only once for each layer whereas
# phase, ke and effective_permittivity can be called several times.
#


def derived_IBA(effective_permittivity_model=polder_van_santen):  # , absorption_calculation=None):
    """return a new IBA model with variant from the default IBA.

    :param effective_permittivity_model: permittivity mixing formula. Must be a function of 4 parameters (frac_volume, e0, es, depol_xyz).

    :returns a new class inheriting from IBA but with patched methods
    """
    new_class_name = "IBA_%s" % (effective_permittivity_model.__name__)  # , absorption_calculation)

    return type(new_class_name, (IBA, ), {'effective_permittivity_model' : staticmethod(effective_permittivity_model)})


class IBA(object):

    """
    Improved Born Approximation electromagnetic model class.

    As with all electromagnetic modules, this class is used to create an electromagnetic
    object that holds information about the effective permittivity, extinction coefficient and
    phase function for a particular snow layer. Due to the frequency dependence, information
    about the sensor is required. Passive and active sensors also have different requirements on
    the size of the phase matrix as redundant information is not calculated for the
    passive case.

    :param sensor: object containing sensor characteristics
    :param layer: object containing snow layer characteristics (single layer)


    **Usage Example:**

        This class is not normally accessed directly by the user, but forms part of the
        smrt model, together with the radiative solver (in this example, `dort`) i.e.:

        ::

            from smrt import make_model
            model = make_model("iba", "dort")

        `iba` does not need to be imported by the user due to autoimport of electromagnetic model modules

    """

    # default effective_permittivity_model is polder_van_santen in Matzler 1998 and Matzler&Wiesman 1999
    effective_permittivity_model = staticmethod(polder_van_santen)


    def __init__(self, sensor, layer):

        # Set size of phase matrix: active needs an extended phase matrix
        if sensor.mode == 'P':
            self.npol = 2
        else:
            self.npol = 3

        # Bring layer and sensor properties into emmodel
        self.frac_volume = layer.frac_volume
        self.microstructure = layer.microstructure  # Do this here, so can pass FT of correlation fn to phase function
        self.e0 = layer.permittivity(0, sensor.frequency)  # background permittivity
        self.eps = layer.permittivity(1, sensor.frequency)  # scatterer permittivity
        self.k0 = 2 * np.pi * sensor.frequency / C_SPEED  # Wavenumber in free space
        self.inclusion_shape = layer.inclusion_shape # for assuming spherical or ellipsoidal inclusions

        # Calculate depolarization factors and iba_coefficient
        self.depol_xyz = depolarization_factors()
        self._effective_permittivity = self.effective_permittivity()
        self.iba_coeff = self.compute_iba_coeff()

        # Absorption coefficient for general lossy medium under assumption of low-loss medium.
        self.ka = self.compute_ka()

        # Calculate scattering coefficient: integrate p11+p12 over mu
        k = 6  # number of samples. This should be adaptative depending on the size/wavelength
        mu = np.linspace(1, -1, 2**k + 1)
        y = self.ks_integrand(mu)
        ks_int = scipy.integrate.romb(y, mu[0] - mu[1])  # integrate between 0 and pi (i.e. mu between -1 and 1)
        self.ks = ks_int / 4.  # Ding et al. (2010), normalised by (1/4pi)

        if not (self.ks >= 0):
            print("ks, the scattering coefficient has an invalid value '%g' in layer nb '%i'" % (self.ks, getattr(layer, 'number', 0)))

    def compute_iba_coeff(self):
        """ Calculate angular independent IBA coefficient: used in both scattering coefficient and phase function calculations

            .. note::

                Requires mean squared field ratio (uses mean_sq_field_ratio method)

        """
        y2 = self.mean_sq_field_ratio(self.e0, self.eps)
        iba_coeff = (1. / (4. * np.pi)) * np.absolute(self.eps - self.e0)**2. * y2 * (self.k0)**4
        return iba_coeff

    def mean_sq_field_ratio(self, e0, eps):
        """ Mean squared field ratio calculation

            Uses layer effective permittivity

            :param e0: background relative permittivity
            :param eps: scattering constituent relative permittivity

        """
        quasi_permittivity = (2. * self._effective_permittivity + e0) / 3.
        y2 = (1. / 3.) * np.sum(np.absolute(quasi_permittivity / (quasi_permittivity + (eps - e0) * self.depol_xyz))**2.)
        return y2

    def basic_check(self):
        # Need to be defined
        pass

    def ks_integrand(self, mu):
        """ This is the scattering function for the IBA model.

        It uses the phase matrix in the 1-2 frame. With incident angle chosen to be 0, the scattering
        angle becomes the scattering zenith angle:

        .. math::

            \\Theta = \\theta


        Scattering coefficient is determined by integration over the scattering angle (0 to \\pi)

        :param mu: cosine of the scattering angle (single angle)

        .. math::

            ks\\_int = p11 + p22

        The integration is performed outside this method.

        """

        # Set up scattering geometry for 1-2 frame
        # Choose incident zenith angle to be 0 so scattering angle = scattering zenith angle (use mhu)
        # phi in the 1-2 frame for calculation of p11 is pi
        # phi in the 1-2 frame for calculation of p22 is pi / 2
        # Calculate wavevector difference
        sintheta_2 = np.sqrt((1. - mu) / 2.)  # = np.sin(theta / 2.)

        k_diff = np.asarray(2. * self.k0 * sintheta_2 * abs(np.sqrt(self._effective_permittivity)))

        # Calculate microstructure term
        if hasattr(self.microstructure, 'ft_autocorrelation_function'):
            ft_corr_fn = self.microstructure.ft_autocorrelation_function(k_diff)
        else:
            raise SMRTError("Fourier Transform of this microstructure model has not been defined, or there is a problem with its calculation")

        p11 = (self.iba_coeff * ft_corr_fn).real * mu**2
        p22 = (self.iba_coeff * ft_corr_fn).real * 1.

        ks_int = (p11 + p22)

        return ks_int.real

    def phase(self, mu_s, mu_i, dphi, npol=2):
        """ IBA Phase function (not decomposed).

"""
        # cos and sin of scattering and incident angles in the main frame
        cos_ti = np.atleast_1d(mu_i)[np.newaxis, np.newaxis, :]
        sin_ti = np.sqrt(1. - cos_ti**2)

        cos_t = np.atleast_1d(mu_s)[np.newaxis, :, np.newaxis]
        sin_t = np.sqrt(1. - cos_t**2)

        dphi = np.atleast_1d(dphi)
        cos_pd = np.cos(dphi)[:, np.newaxis, np.newaxis]
        sin_pd_sign = np.where(dphi >= np.pi, -1, 1)[:, np.newaxis, np.newaxis]

        # Scattering angle in the 1-2 frame
        cosT = np.clip(cos_t * cos_ti + sin_t * sin_ti * cos_pd, -1.0, 1.0)  # Prevents occasional numerical error
        cosT2 = cosT**2  # cos^2 (Theta)
        sinT = np.sqrt(1. - cosT2)

        # Apply non-zero scattering denominator
        nonnullsinT = sinT >= 1e-6

        # Create arrays of rotation angles
        cost_sinti = cos_t * sin_ti
        costi_sint = cos_ti * sin_t

        cos_i1 = cost_sinti - costi_sint * cos_pd
        np.divide(cos_i1, sinT, where=nonnullsinT, out=cos_i1)
        np.clip(cos_i1, -1.0, 1.0, out=cos_i1)

        cos_i2 = costi_sint - cost_sinti * cos_pd
        np.divide(cos_i2, sinT, where=nonnullsinT, out=cos_i2)
        np.clip(cos_i2, -1.0, 1.0, out=cos_i2)

        # Special condition if theta and theta_i = 0 to preserve azimuth dependency
        dege_dphi = np.broadcast_to((sin_t < 1e-6) & (sin_ti < 1e-6), cos_i1.shape)
        cos_i1[dege_dphi] = 1.
        cos_i2[dege_dphi] = np.broadcast_to(cos_pd, cos_i2.shape)[dege_dphi]

        # # See Matzler 2006 pg 111 Eq. 3.20
        # # Calculate rotation angles alpha, alpha_i
        # # Convention follows Matzler 2006, Thermal Microwave Radiation, p111, eqn 3.20

        Li = Lmatrix(cos_i1, -sin_pd_sign, (3, npol))    # L (-i1)

        if npol == 2:
            RLi = np.array([[cosT2 * Li[0][0], cosT2 * Li[0][1]],
                            Li[1], [cosT * Li[2][0], cosT * Li[2][1]]])

        elif npol == 3:
            RLi = np.array([[cosT2 * Li[0][0], cosT2 * Li[0][1], cosT2 * Li[0][2]],
                            Li[1], [cosT * Li[2][0], cosT * Li[2][1], cosT * Li[2][2]]])
        else:
            raise RuntimeError("invalid value of npol")

        Ls = Lmatrix(-cos_i2, sin_pd_sign, (npol, 3))    # L (pi - i2)
        p = np.einsum('ij...,jk...->ik...', Ls, RLi)   # multiply the outer dimension (=polarization)

        # IBA phase function = rayleigh phase function * angular part of microstructure term
        k_diff = 2. * self.k0 * np.sqrt(self._effective_permittivity) * np.sqrt(0.5 - 0.5 * cosT)

        # Calculate microstructure term
        if hasattr(self.microstructure, 'ft_autocorrelation_function'):
            ft_corr_fn = self.microstructure.ft_autocorrelation_function(k_diff)
        else:
            raise SMRTError("Fourier Transform of this microstructure model has not been defined, or there is a problem with its calculation")

        return smrt_matrix(ft_corr_fn * self.iba_coeff * p)
        
    def ft_even_phase(self, mu_s, mu_i, m_max, npol=None):
        """ Calculation of the Fourier decomposed IBA phase function.

        This method calculates the Improved Born Approximation phase matrix for all
        Fourier decomposition modes and return the output.

        Coefficients within the phase function are

        Passive case (m = 0 only) and active (m = 0) ::

            M  = [Pvvp  Pvhp]
                 [Phvp  Phhp]

        Active case (m > 0)::

            M =  [Pvvp Pvhp Pvup]
                 [Phvp Phhp Phup]
                 [Puvp Puhp Puup]


        The IBA phase function is given in Mätzler, C. (1998). Improved Born approximation for
        scattering of radiation in a granular medium. *Journal of Applied Physics*, 83(11),
        6111-6117. Here, calculation of the phase matrix is based on the phase matrix in
        the 1-2 frame, which is then rotated according to the incident and scattering angles,
        as described in e.g. *Thermal Microwave Radiation: Applications for Remote Sensing, Mätzler (2006)*.
        Fourier decomposition is then performed to separate the azimuthal dependency from the incidence angle dependency.

        :param mu_s: 1-D array of cosine of viewing radiation stream angles (set by solver)
        :param mu_i: 1-D array of cosine of incident radiation stream angles (set by solver)
        :param m_max: maximum Fourier decomposition mode needed
        :param npol: number of polarizations considered (set from sensor characteristics)

        """

        if npol is None:
            npol = self.npol  # npol is set from sensor mode except in call to energy conservation test

        # Raise exception if mu = 1 ever called for active: p13, p23, p31, p32 signs incorrect
        if np.any(mu_i == 1) and npol > 2:
            raise SMRTError("Phase matrix signs for sine elements of mode m = 2 incorrect")

        # compute the phase function
        def phase_function(dphi):
            return self.phase(mu_s, mu_i, dphi, npol)

        return generic_ft_even_matrix(phase_function, m_max)  # order is pola_s, pola_i, m, mu_s, mu_i

    def compute_ka(self):
        """ IBA absorption coefficient calculated from the low-loss assumption of a general lossy medium.

        Calculates ka from wavenumber in free space (determined from sensor), and effective permittivity
        of the medium (snow layer property)

        :return ka: absorption coefficient [m :sup:`-1`]

        .. note::

            This may not be suitable for high density material

        """

        # after several go and back, the situation is now clear:
        # MEMLS uses the formulation in IBA98 paper. In SMRT this formulation is available in iba_original.py
        # here we use Polden von Staten which is known to be better and accommodate the full range of density/frac_volume
        # PvS is also now recommended by Christian Matzler and has been implemented in MEMLS modified for sea-ice.
        # This is therefore the default in SMRT. The fully MEMLS compatible IBA is in iba_original.py

        return 2 * self.k0 * np.sqrt(self._effective_permittivity).imag

    def ke(self, mu):
        """ IBA extinction coefficient matrix

        The extinction coefficient is defined as the sum of scattering and absorption
        coefficients. However, the radiative transfer solver requires this in matrix form,
        so this method is called by the solver.

            :param mu: 1-D array of cosines of radiation stream incidence angles
            :returns ke: extinction coefficient matrix [m :sup:`-1`]

            .. note::

                Spherical isotropy assumed (all elements in matrix are identical).

                Size of extinction coefficient matrix depends on number of radiation
                streams, which is set by the radiative transfer solver.

        """
        return np.full(len_atleast_1d(mu), self.ks + self.ka)

    def effective_permittivity(self):
        """ Calculation of complex effective permittivity of the medium.

        :returns effective_permittivity: complex effective permittivity of the medium

        """

        eps = type(self).effective_permittivity_model(
            self.frac_volume, self.e0, self.eps, self.depol_xyz, self.inclusion_shape)

        if eps.imag < 0:
            raise SMRTError("the imaginary part of the permittivity must be positive, by convention, in SMRT")
        return eps


class IBA_MM(IBA):
    # Undocumented: this is test code for comparison with MEMLS, and may be removed from later versions.

    def __init__(self, sensor, layer):
        # Gives all IBA parameters. Some need to be recalculated (effective permittivity, scattering and absorption coefficients):
        IBA.__init__(self, sensor, layer)

        self._effective_permittivity = polder_van_santen(self.frac_volume)

        # Imaginary component for effective permittivity from Wiesmann and Matzler (1999)
        y2 = self.mean_sq_field_ratio(self.e0, self.eps)
        effective_permittivity_imag = self.frac_volume * self.eps.imag * y2 * np.sqrt(self._effective_permittivity)
        self._effective_permittivity = self._effective_permittivity + 1j * effective_permittivity_imag

        self.iba_coeff = self.compute_iba_coeff()
        ks_int, ks_err = scipy.integrate.quad(self._mm_integrand, 0, np.pi)
        self.ks = ks_int / 2.  # Matzler and Wiesmann, RSE, 1999, eqn (8)
        # General lossy medium under assumption of low-loss medium.
        self.ka = self.compute_ka()

    def _mm_integrand(self, theta):
        # Calculate wavevector difference
        k_diff = np.asarray(2. * self.k0 * np.sin(theta / 2.) * np.sqrt(self._effective_permittivity))

        # Calculate microstructure term
        if hasattr(self.microstructure, 'ft_autocorrelation_function'):
            ft_corr_fn = self.microstructure.ft_autocorrelation_function(k_diff)
        else:
            raise SMRTError("Fourier Transform of this microstructure model has not been defined, or there is a problem with its calculation")

        # MEMLS phase function has mean of H and V polarisation angle. Eqn 17c of Matzler and Wiesmann 1999.
        p_mm = self.iba_coeff * ft_corr_fn.real * (1. - 0.5 * np.square(np.sin(theta)))
        ks_int = p_mm * np.sin(theta)

        return ks_int.real


def Lmatrix(cos_phi, sin_phi_sign, npol):

    # Calculate arrays of rotated phase matrix elements
    # Shorthand to make equations shorter & marginally faster to compute
    cos2_phi = cos_phi**2  # cos^2 (phi)
    sin2_phi = 1 - cos2_phi  # sin^2 (phi)

    sin_2phi = 2 * cos_phi * np.sqrt(sin2_phi)  # sin(2 phi_i)
    sin_2phi *= sin_phi_sign

    if npol == (2, 3):
        s05 = 0.5 * sin_2phi
        L = [[cos2_phi, sin2_phi, s05],
             [sin2_phi, cos2_phi, -s05]]
    elif npol == (3, 2):
        L = [[cos2_phi, sin2_phi],
             [sin2_phi, cos2_phi],
             [-sin_2phi, sin_2phi]]
    else:  # 3 pol
        s05 = 0.5 * sin_2phi
        cos_2phi = 2 * cos2_phi - 1  # cos(2 alpha)
        L = [[cos2_phi, sin2_phi, s05],
             [sin2_phi, cos2_phi, -s05],
             [-sin_2phi, sin_2phi, cos_2phi]]
    return L
