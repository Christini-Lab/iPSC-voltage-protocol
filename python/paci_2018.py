from math import log, sqrt
from typing import List

import numpy as np
from scipy import integrate

import ga_configs
import protocols
import trace


class PaciModel:
    """An implementation of the Paci2018 model by Paci et al.

    Attributes:
        default_parameters: A dict containing tunable parameters along with
            their default values as specified in Paci et al.
        updated_parameters: A dict containing all parameters that are being
            tuned.
    """

    # Parameters from optimizer.
    vmax_up_millimolar_per_second = 0.5113
    g_irel_max_millimolar_per_second = 62.5434
    ry_ra_1_micromolar = 0.05354
    ry_ra_2_micromolar = 0.0488
    ry_rahalf_micromolar = 0.02427
    ry_rohalf_micromolar = 0.01042
    ry_rchalf_micromolar = 0.00144
    k_na_ca_a_per_f = 3917.0463
    p_na_k_a_per_f = 2.6351
    k_up_millimolar = 3.1928e-4
    v_leak_per_second = 4.7279e-4
    alpha_in_i_na_ca = 2.5371

    # Constants (in model parameters).
    f_coulomb_per_mole = 96485.3415
    r_joule_per_mole_kelvin = 8.314472
    t_kelvin = 310.0

    # Cell geometry
    v_sr_micrometer_cube = 583.73
    vc_micrometer_cube = 8800.0
    cm_farad = 9.87109e-11

    # Extracellular concentrations
    nao_millimolar = 151.0
    ko_millimolar = 5.4
    cao_millimolar = 1.8

    # Intracellular concentrations
    ki_millimolar = 150.0

    # Other variables
    t_drug_application = 10000
    i_na_f_red_med = 1
    i_ca_l_red_med = 1
    i_kr_red_med = 1
    i_ks_red_med = 1

    y_names = ['Vm', 'Ca_SR', 'Cai', 'g', 'd', 'f1', 'f2', 'fCa', 'Xr1', 'Xr2',
               'Xs', 'h', 'j', 'm', 'Xf', 'q', 'r', 'Nai', 'm_L', 'h_L', 'RyRa',
               'RyRo', 'RyRc']
    y_initial = [-0.0749228904740065, 0.0936532528714175, 3.79675694306440e-05,
                 0, 8.25220533963093e-05, 0.741143500777858, 0.999983958619179,
                 0.997742015033076, 0.266113517200784, 0.434907203275640,
                 0.0314334976383401, 0.745356534740988, 0.0760523580322096,
                 0.0995891726023512, 0.0249102482276486, 0.841714924246004,
                 0.00558005376429710, 8.64821066193476, 0.00225383437957339,
                 0.0811507312565017, 0.0387066722172937, 0.0260449185736275,
                 0.0785849084330126]
    y_initial_zero = [-0.070, 0.32, 0.0002, 0, 0, 1, 1, 1, 0, 1, 0, 0.75, 0.75,
                      0, 0.1, 1, 0, 9.2, 0, 0.75, 0.3, 0.9, 0.1]

    def __init__(self, updated_parameters=None):
        self.default_parameters = {'g_na': 3671.2302, 'g_ca_l': 8.635702e-5,
                                   'g_f_s': 30.10312, 'g_ks_s': 2.041,
                                   'g_kr_s': 29.8667, 'g_k1_s': 28.1492,
                                   'g_p_ca': 0.4125, 'g_b_na': 0.95,
                                   'g_b_ca': 0.727272,
                                   'g_na_lmax': 17.25}
        if updated_parameters:
            self.default_parameters.update(updated_parameters)
        self.t = []
        self.y_voltage = []
        self.d_y_voltage = []
        self.current_response_info = None
        self.full_y = []

    def generate_response(self, protocol):
        """Returns a trace based on the specified target objective.

        Args:
            protocol: An object of a specified protocol.

        Returns:
            A Trace object representing the change in membrane potential over
            time.
        """
        # Reset instance variables when model is run again.
        self.t = []
        self.y_voltage = []
        self.d_y_voltage = []
        self.full_y = []

        if isinstance(protocol, protocols.SingleActionPotentialProtocol):
            self.current_response_info = trace.CurrentResponseInfo()
            try:
                integrate.solve_ivp(
                    self.generate_single_action_potential_function(),
                    [0, protocol.duration],
                    # TODO CHANGED Y INITIAL TO 0
                    self.y_initial_zero,
                    method='BDF',
                    max_step=1e-3)
            except ValueError:
                print('Model could not produce trace.')
                return None
            return trace.Trace(
                self.t,
                self.y_voltage,
                current_response_info=self.current_response_info)

        elif isinstance(protocol, protocols.IrregularPacingProtocol):
            pacing_info = trace.IrregularPacingInfo()
            try:
                integrate.solve_ivp(
                    self.generate_irregular_pacing_function(
                        protocol, pacing_info),
                    [0, protocol.duration],
                    self.y_initial,
                    method='BDF',
                    max_step=1e-3)
            except ValueError:
                return None
            return trace.Trace(self.t, self.y_voltage, pacing_info=pacing_info)

        elif isinstance(protocol, protocols.VoltageClampProtocol):
            self.current_response_info = trace.CurrentResponseInfo(
                protocol=protocol)
            try:
                integrate.solve_ivp(
                    self.generate_voltage_clamp_function(protocol),
                    [0, protocol.voltage_change_endpoints[-1]],
                    self.y_initial,
                    method='BDF',
                    max_step=1e-3)
            except ValueError:
                return None
            return trace.Trace(
                self.t,
                self.y_voltage,
                current_response_info=self.current_response_info)

    def generate_single_action_potential_function(self):

        def single_action_potential(t, y):
            return self.action_potential_diff_eq(t, y)

        return single_action_potential

    def generate_irregular_pacing_function(self, protocol, pacing_info):
        offset_times = protocol.make_offset_generator()

        def irregular_pacing(t, y):
            d_y = self.action_potential_diff_eq(t, y)

            if pacing_info.detect_peak(self.t, y[0], self.d_y_voltage):
                pacing_info.peaks.append(t)
                voltage_diff = abs(pacing_info.AVG_AP_START_VOLTAGE - y[0])
                pacing_info.apd_90_end_voltage = y[0] - voltage_diff * 0.9

            if pacing_info.detect_apd_90(y[0]):
                try:
                    pacing_info.add_apd_90(t)
                    pacing_info.stimulations.append(t + next(offset_times))
                except StopIteration:
                    pass

            if pacing_info.should_stimulate(t):
                i_stimulation = protocol.STIM_AMPLITUDE_AMPS / self.cm_farad
            else:
                i_stimulation = 0.0

            d_y[0] += i_stimulation
            return d_y

        return irregular_pacing

    def generate_voltage_clamp_function(self, protocol):

        def voltage_clamp(t, y):
            y[0] = protocol.get_voltage_at_time(t)
            return self.action_potential_diff_eq(t, y)

        return voltage_clamp

    def action_potential_diff_eq(self, t, y):
        self.y_voltage.append(y[0])
        self.t.append(t)
        self.full_y.append(y)

        d_y = np.empty(23)

        # Nernst potential
        e_na = self.r_joule_per_mole_kelvin * \
               self.t_kelvin / self.f_coulomb_per_mole * log(
            self.nao_millimolar / y[17])

        e_ca = 0.5 * self.r_joule_per_mole_kelvin * self.t_kelvin / self.f_coulomb_per_mole * log(
            self.cao_millimolar / y[2])

        e_k = self.r_joule_per_mole_kelvin * self.t_kelvin / self.f_coulomb_per_mole * log(
            self.ko_millimolar / self.ki_millimolar)
        pk_na = 0.03
        e_ks = self.r_joule_per_mole_kelvin * self.t_kelvin / self.f_coulomb_per_mole * log(
            (self.ko_millimolar + pk_na * self.nao_millimolar) / (
                    self.ki_millimolar + pk_na * y[17]))

        # iNa
        i_na = ((t < self.t_drug_application) * 1 + (
                    t >= self.t_drug_application)
                * self.i_na_f_red_med) * self.default_parameters['g_na'] * y[
                   13] ** 3.0 * y[11] * y[12] * \
               (y[0] - e_na)

        h_inf = 1.0 / sqrt(1.0 + np.exp((y[0] * 1000.0 + 72.1) / 5.7))
        alpha_h = 0.057 * np.exp(-(y[0] * 1000.0 + 80.0) / 6.8)
        beta_h = 2.7 * np.exp(
            0.079 * y[0] * 1000.0) + 3.1 * 10.0 ** 5.0 * np.exp(
            0.3485 * y[0] * 1000.0)
        if y[0] < -0.0385:
            tau_h = 1.5 / ((alpha_h + beta_h) * 1000.0)
        else:
            tau_h = 1.5 * 1.6947 / 1000.0

        d_y[11] = (h_inf - y[11]) / tau_h

        j_inf = 1.0 / sqrt(1.0 + np.exp((y[0] * 1000.0 + 72.1) / 5.7))
        if y[0] < -0.04:
            alpha_j = (-25428.0 * np.exp(
                0.2444 * y[0] * 1000.0) - 6.948 * 10.0 ** -6.0
                       * np.exp(-0.04391 * y[0] * 1000.0)) * (
                              y[0] * 1000.0 + 37.78) \
                      / (1.0 + np.exp(0.311 * (y[0] * 1000.0 + 79.23)))
        else:
            alpha_j = 0.0

        if y[0] < -0.04:
            beta_j = ((0.02424 * np.exp(-0.01052 * y[0] * 1000) / (
                    1 + np.exp(-0.1378 * (y[0] * 1000 + 40.14)))))
        else:
            beta_j = ((0.6 * np.exp(0.057 * y[0] * 1000) /
                       (1 + np.exp(-0.1 * (y[0] * 1000 + 32)))))

        tau_j = 7.0 / ((alpha_j + beta_j) * 1000.0)
        d_y[12] = (j_inf - y[12]) / tau_j

        m_inf = 1.0 / (1.0 + np.exp((-y[0] * 1000.0 - 34.1) / 5.9)) ** (
                    1.0 / 3.0)
        alpha_m = 1.0 / (1.0 + np.exp((-y[0] * 1000.0 - 60.0) / 5.0))
        beta_m = 0.1 / (1.0 + np.exp((y[0] * 1000.0 + 35.0) / 5.0)) + 0.1 / (
                1.0 + np.exp((y[0] * 1000.0 - 50.0) / 200.0))
        tau_m = 1.0 * alpha_m * beta_m / 1000.0
        d_y[13] = (m_inf - y[13]) / tau_m

        # i NaL
        my_coef_tau_m = 1
        tau_i_na_l_ms = 200
        vh_h_late = 87.61
        i_na_l = self.default_parameters['g_na_lmax'] * y[18] ** 3 * y[19] * (
                    y[0] - e_na)

        m_inf_l = 1 / (1 + np.exp(-(y[0] * 1000 + 42.85) / 5.264))
        alpha_m_l = 1 / (1 + np.exp((-60 - y[0] * 1000) / 5))
        beta_m_l = 0.1 / (1 + np.exp((y[0] * 1000 + 35) / 5)) + 0.1 / (
                1 + np.exp((y[0] * 1000 - 50) / 200))
        tau_m_l = 1 / 1000 * my_coef_tau_m * alpha_m_l * beta_m_l
        d_y[18] = (m_inf_l - y[18]) / tau_m_l

        h_inf_l = 1 / (1 + np.exp((y[0] * 1000 + vh_h_late) / (7.488)))
        tau_h_l = 1 / 1000 * tau_i_na_l_ms
        d_y[19] = (h_inf_l - y[19]) / tau_h_l

        # i f
        e_f_volt = -0.017
        i_f = self.default_parameters['g_f_s'] * y[14] * (y[0] - e_f_volt)
        i_f_na = 0.42 * self.default_parameters['g_f_s'] * y[14] * (
                    y[0] - e_na)

        xf_infinity = 1.0 / (1.0 + np.exp((y[0] * 1000.0 + 77.85) / 5.0))
        tau_xf = 1900.0 / (1.0 + np.exp((y[0] * 1000.0 + 15.0) / 10.0)) / 1000.0
        d_y[14] = (xf_infinity - y[14]) / tau_xf

        # i CaL
        i_ca_l = ((t < self.t_drug_application) * 1 + (
                    t >= self.t_drug_application) *
                  self.i_ca_l_red_med) * self.default_parameters[
                     'g_ca_l'] * 4.0 * y[0] \
                 * self.f_coulomb_per_mole ** 2.0 / (
                         self.r_joule_per_mole_kelvin * self.t_kelvin) * \
                 (y[2] * np.exp(2.0 * y[0] * self.f_coulomb_per_mole / (
                         self.r_joule_per_mole_kelvin * self.t_kelvin)) - 0.341 *
                  self.cao_millimolar) / (
                             np.exp(2.0 * y[0] * self.f_coulomb_per_mole / (
                                     self.r_joule_per_mole_kelvin * self.t_kelvin)) - 1.0) * \
                 y[4] * y[5] * y[
                     6] * y[7]

        d_infinity = 1.0 / (1.0 + np.exp(-(y[0] * 1000.0 + 9.1) / 7.0))
        alpha_d = 0.25 + 1.4 / (1.0 + np.exp((-y[0] * 1000.0 - 35.0) / 13.0))
        beta_d = 1.4 / (1.0 + np.exp((y[0] * 1000.0 + 5.0) / 5.0))
        gamma_d = 1.0 / (1.0 + np.exp((-y[0] * 1000.0 + 50.0) / 20.0))
        tau_d = (alpha_d * beta_d + gamma_d) * 1.0 / 1000.0
        d_y[4] = (d_infinity - y[4]) / tau_d

        f1_inf = 1.0 / (1.0 + np.exp((y[0] * 1000.0 + 26.0) / 3.0))
        if f1_inf - y[5] > 0.0:
            const_f1 = 1.0 + 1433.0 * (y[2] - 50.0 * 1.0e-6)
        else:
            const_f1 = 1.0

        tau_f1 = (20.0 + 1102.5 * np.exp(
            -((y[0] * 1000.0 + 27.0) ** 2.0 / 15.0) ** 2.0) + 200.0 / (
                          1.0 + np.exp(
                      (13.0 - y[0] * 1000.0) / 10.0)) + 180.0 / (
                          1.0 + np.exp(
                      (30.0 + y[0] * 1000.0) / 10.0))) * const_f1 / 1000.0
        d_y[5] = (f1_inf - y[5]) / tau_f1

        f2_inf = 0.33 + 0.67 / (1.0 + np.exp((y[0] * 1000.0 + 32.0) / 4.0))
        const_f2 = 1.0
        tau_f2 = (600.0 * np.exp(
            -(y[0] * 1000.0 + 25.0) ** 2.0 / 170.0) + 31.0 / (
                          1.0 + np.exp(
                      (25.0 - y[0] * 1000.0) / 10.0)) + 16.0 / (1.0 + np.exp(
            (30.0 + y[0] * 1000.0) / 10.0))) * const_f2 / 1000.0
        d_y[6] = (f2_inf - y[6]) / tau_f2

        alpha_f_ca = 1.0 / (1.0 + (y[2] / 0.0006) ** 8.0)
        beta_f_ca = 0.1 / (1.0 + np.exp((y[2] - 0.0009) / 0.0001))
        gamma_f_ca = 0.3 / (1.0 + np.exp((y[2] - 0.00075) / 0.0008))
        f_ca_inf = (alpha_f_ca + beta_f_ca + gamma_f_ca) / 1.3156
        if y[0] > -0.06 and f_ca_inf > y[7]:
            const_f_ca = 0.0
        else:
            const_f_ca = 1.0

        tau_f_ca = 0.002  # second (in i_CaL_fCa_gate)
        d_y[7] = const_f_ca * (f_ca_inf - y[7]) / tau_f_ca

        # i to
        g_to_s_per_f = 29.9038
        i_to = g_to_s_per_f * (y[0] - e_k) * y[15] * y[16]

        q_inf = 1.0 / (1.0 + np.exp((y[0] * 1000.0 + 53.0) / 13.0))
        tau_q = (6.06 + 39.102 / (
                0.57 * np.exp(-0.08 * (y[0] * 1000.0 + 44.0)) + 0.065 *
                np.exp(0.1 * (y[0] * 1000.0 + 45.93)))) / 1000.0
        d_y[15] = (q_inf - y[15]) / tau_q

        r_inf = 1.0 / (1.0 + np.exp(-(y[0] * 1000.0 - 22.3) / 18.75))
        tau_r = (2.75352 + 14.40516 / (
                1.037 * np.exp(0.09 * (y[0] * 1000.0 + 30.61)) + 0.369 * np.exp(
            -0.12 * (y[0] * 1000.0 + 23.84)))) / 1000.0
        d_y[16] = (r_inf - y[16]) / tau_r

        # i Ks
        i_ks = ((t < self.t_drug_application) * 1 + (
                    t >= self.t_drug_application) *
                self.i_ks_red_med) * self.default_parameters['g_ks_s'] * (
                           y[0] - e_ks) * y[10] ** 2.0 * \
               (1.0 + 0.6 / (1.0 + (3.8 * 0.00001 / y[2]) ** 1.4))

        xs_infinity = 1.0 / (1.0 + np.exp((-y[0] * 1000.0 - 20.0) / 16.0))
        alpha_xs = 1100.0 / sqrt(1.0 + np.exp((-10.0 - y[0] * 1000.0) / 6.0))
        beta_xs = 1.0 / (1.0 + np.exp((-60.0 + y[0] * 1000.0) / 20.0))
        tau_xs = 1.0 * alpha_xs * beta_xs / 1000.0
        d_y[10] = (xs_infinity - y[10]) / tau_xs

        # i Kr
        l0 = 0.025
        q = 2.3  # dimensionless (in i_Kr_Xr1_gate)
        i_kr = ((t < self.t_drug_application) * 1 + (
                t >= self.t_drug_application) * self.i_kr_red_med) * \
               self.default_parameters['g_kr_s'] * (
                       y[0] - e_k) * y[8] * y[9] * sqrt(
            self.ko_millimolar / 5.4)

        v_half = 1000.0 * (-self.r_joule_per_mole_kelvin * self.t_kelvin / (
                self.f_coulomb_per_mole * q) * log(
            (1.0 + self.cao_millimolar / 2.6) ** 4.0 / (
                    l0 * (1.0 + self.cao_millimolar / 0.58) ** 4.0)) - 0.019)

        xr1_inf = 1.0 / (1.0 + np.exp((v_half - y[0] * 1000.0) / 4.9))
        alpha_xr1 = 450.0 / (1.0 + np.exp((-45.0 - y[0] * 1000.0) / 10.0))
        beta_xr1 = 6.0 / (1.0 + np.exp((30.0 + y[0] * 1000.0) / 11.5))
        tau_xr1 = 1.0 * alpha_xr1 * beta_xr1 / 1000.0
        d_y[8] = (xr1_inf - y[8]) / tau_xr1

        xr2_infinity = 1.0 / (1.0 + np.exp((y[0] * 1000.0 + 88.0) / 50.0))
        alpha_xr2 = 3.0 / (1.0 + np.exp((-60.0 - y[0] * 1000.0) / 20.0))
        beta_xr2 = 1.12 / (1.0 + np.exp((-60.0 + y[0] * 1000.0) / 20.0))
        tau_xr2 = 1.0 * alpha_xr2 * beta_xr2 / 1000.0
        d_y[9] = (xr2_infinity - y[9]) / tau_xr2

        # i K1
        alpha_k1 = 3.91 / (
                1.0 + np.exp(0.5942 * (y[0] * 1000.0 - e_k * 1000.0 - 200.0)))
        beta_k1 = (-1.509 * np.exp(
            0.0002 * (y[0] * 1000.0 - e_k * 1000.0 + 100.0)) + np.exp(
            0.5886 * (y[0] * 1000.0 - e_k * 1000.0 - 10.0))) / (
                          1.0 + np.exp(0.4547 * (y[0] * 1000.0 - e_k * 1000.0)))
        xk1_inf = alpha_k1 / (alpha_k1 + beta_k1)
        i_k1 = self.default_parameters['g_k1_s'] * xk1_inf * (
                    y[0] - e_k) * sqrt(self.ko_millimolar / 5.4)

        # i NaCa
        km_ca_millimolar = 1.38
        km_nai_millimolar = 87.5
        ksat = 0.1
        gamma = 0.35
        k_na_ca1_a_per_f = self.k_na_ca_a_per_f
        i_na_ca = (
                k_na_ca1_a_per_f
                * (
                        np.exp(
                            gamma
                            * y[0]
                            * self.f_coulomb_per_mole
                            / (self.r_joule_per_mole_kelvin * self.t_kelvin)
                        )
                        * y[17] ** 3.0
                        * self.cao_millimolar
                        - np.exp(
                    (gamma - 1.0)
                    * y[0]
                    * self.f_coulomb_per_mole
                    / (self.r_joule_per_mole_kelvin * self.t_kelvin)
                )
                        * self.nao_millimolar ** 3.0
                        * y[2]
                        * self.alpha_in_i_na_ca
                )
                / (
                        (km_nai_millimolar ** 3.0 + self.nao_millimolar ** 3.0)
                        * (km_ca_millimolar + self.cao_millimolar)
                        * (
                                1.0
                                + ksat
                                * np.exp(
                            (gamma - 1.0)
                            * y[0]
                            * self.f_coulomb_per_mole
                            / (self.r_joule_per_mole_kelvin * self.t_kelvin)
                        )
                        )
                )
        )

        # i NaK
        km_k_millimolar = 1.0
        km_na_millimolar = 40.0
        p_na_k1 = self.p_na_k_a_per_f
        i_na_k = (
                p_na_k1
                * self.ko_millimolar
                / (self.ko_millimolar + km_k_millimolar)
                * y[17]
                / (y[17] + km_na_millimolar)
                / (
                        1.0
                        + 0.1245
                        * np.exp(
                    -0.1
                    * y[0]
                    * self.f_coulomb_per_mole
                    / (self.r_joule_per_mole_kelvin * self.t_kelvin)
                )
                        + 0.0353
                        * np.exp(
                    -y[0]
                    * self.f_coulomb_per_mole
                    / (self.r_joule_per_mole_kelvin * self.t_kelvin)
                )
                )
        )

        # i pCa
        kp_ca_millimolar = 0.0005
        i_p_ca = self.default_parameters['g_p_ca'] * y[2] / (
                    y[2] + kp_ca_millimolar)

        # Background currents
        i_b_na = self.default_parameters['g_b_na'] * (y[0] - e_na)

        i_b_ca = self.default_parameters['g_b_ca'] * (y[0] - e_ca)

        # Sarcoplasmic reticulum
        i_up = self.vmax_up_millimolar_per_second / (
                1.0 + self.k_up_millimolar ** 2.0 / y[2] ** 2.0)

        i_leak = (y[1] - y[2]) * self.v_leak_per_second

        d_y[3] = 0

        # RyR
        ry_rsr_cass = (1 - 1 / (1 + np.exp((y[1] - 0.3) / 0.1)))
        i_rel = self.g_irel_max_millimolar_per_second * ry_rsr_cass * y[21] * y[
            22] * (
                        y[1] - y[2])

        ry_rainfss = self.ry_ra_1_micromolar - self.ry_ra_2_micromolar / (
                1 + np.exp((1000 * y[2] - self.ry_rahalf_micromolar) / 0.0082))
        ry_rtauadapt = 1
        d_y[20] = (ry_rainfss - y[20]) / ry_rtauadapt

        ry_roinfss = (1 - 1 / (
                1 + np.exp(
            (1000 * y[2] - (y[20] + self.ry_rohalf_micromolar)) / 0.003)))
        if ry_roinfss >= y[21]:
            ry_rtauact = 18.75e-3
        else:
            ry_rtauact = 0.1 * 18.75e-3

        d_y[21] = (ry_roinfss - y[21]) / ry_rtauact

        ry_rcinfss = (1 / (1 + np.exp(
            (1000 * y[2] - (y[20] + self.ry_rchalf_micromolar)) / 0.001)))
        if ry_rcinfss >= y[22]:
            ry_rtauinact = 2 * 87.5e-3
        else:
            ry_rtauinact = 87.5e-3

        d_y[22] = (ry_rcinfss - y[22]) / ry_rtauinact

        # Ca2+ buffering
        buf_c_millimolar = 0.25
        buf_sr_millimolar = 10.0
        kbuf_c_millimolar = 0.001
        kbuf_sr_millimolar = 0.3
        cai_bufc = 1.0 / (
                1.0 + buf_c_millimolar * kbuf_c_millimolar / (
                y[2] + kbuf_c_millimolar) ** 2.0)
        ca_sr_buf_sr = 1.0 / (
                1.0 + buf_sr_millimolar * kbuf_sr_millimolar / (
                y[1] + kbuf_sr_millimolar) ** 2.0)

        # Ionic concentrations
        # Nai
        d_y[17] = -self.cm_farad * (
                i_na + i_na_l + i_b_na + 3.0 * i_na_k + 3.0 * i_na_ca + i_f_na
        ) / (self.f_coulomb_per_mole * self.vc_micrometer_cube * 1.0e-18)
        # caSR
        d_y[2] = cai_bufc * (i_leak - i_up + i_rel - (
                i_ca_l + i_b_ca + i_p_ca - 2.0 * i_na_ca) * self.cm_farad / (
                                     2.0 * self.vc_micrometer_cube *
                                     self.f_coulomb_per_mole * 1.0e-18))
        # Cai
        d_y[
            1] = ca_sr_buf_sr * self.vc_micrometer_cube / self.v_sr_micrometer_cube * (
                i_up - (i_rel + i_leak))

        # Membrane potential
        d_y[0] = -(i_k1 + i_to + i_kr + i_ks + i_ca_l + i_na_k + i_na + i_na_l +
                   i_na_ca + i_p_ca + i_f + i_b_na + i_b_ca)

        if self.current_response_info:
            current_timestep = [
                trace.Current(name='i_k1', value=i_k1),
                trace.Current(name='i_to', value=i_to),
                trace.Current(name='i_kr', value=i_kr),
                trace.Current(name='i_ks', value=i_ks),
                trace.Current(name='i_ca_l', value=i_ca_l),
                trace.Current(name='i_na_k', value=i_na_k),
                trace.Current(name='i_na', value=i_na),
                trace.Current(name='i_na_l', value=i_na_l),
                trace.Current(name='i_na_ca', value=i_na_ca),
                trace.Current(name='i_p_ca', value=i_p_ca),
                trace.Current(name='i_f', value=i_f),
                trace.Current(name='i_b_na', value=i_b_na),
                trace.Current(name='i_b_ca', value=i_b_ca),
            ]
            self.current_response_info.currents.append(current_timestep)

        self.d_y_voltage.append(d_y[0])
        return d_y


def generate_trace(protocol: protocols.PROTOCOL_TYPE,
                   tunable_parameters: List[ga_configs.Parameter]=None,
                   params: List[float]=None) -> trace.Trace:
    """Generates a trace.

    Leave `params` argument empty if generating baseline trace with
    default parameter values.

    Args:
        tunable_parameters: List of tunable parameters.
        protocol: A protocol object used to generate the trace.
        params: A set of parameter values (where order must match with ordered
            labels in `tunable_parameters`).

    Returns:
        A Trace object.
    """
    new_params = dict()
    if params and tunable_parameters:
        for i in range(len(params)):
            new_params[tunable_parameters[i].name] = params[i]

    return PaciModel(
        updated_parameters=new_params).generate_response(protocol)
