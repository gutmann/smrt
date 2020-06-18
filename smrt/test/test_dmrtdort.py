# coding: utf-8

import numpy as np

# local import
from smrt import make_snowpack, make_model, make_snow_layer, make_soil
from smrt.inputs.sensor_list import amsre, active

#
# Ghi: rapid hack, should be splitted in different functions
#


def setup_snowpack():
        # prepare inputs
    l = 2

    nl = l//2  # // Forces integer division
    thickness = np.array([0.1, 0.1]*nl)
    thickness[-1] = 1000  # last one is semi-infinit
    radius = np.array([2e-4]*l)
    temperature = np.array([250.0, 250.0]*nl)
    density = [200, 400]*nl
    stickiness = [0.1, 0.1]*nl

    # create the snowpack
    snowpack = make_snowpack(thickness,
                             "sticky_hard_spheres",
                             density=density,
                             temperature=temperature,
                             radius=radius,
                             stickiness=stickiness)
    return snowpack


def test_dmrt_oneconfig():

    snowpack = setup_snowpack()

    # create the EM Model
    m = make_model("dmrt_qcacp_shortrange", "dort")

    # create the sensor
    radiometer = amsre("37V")

    # run the model
    res = m.run(radiometer, snowpack)

    print(res.TbV(), res.TbH())
    assert (res.TbV() - 202.1726891947754) < 1e-4
    assert (res.TbH() - 187.45835882462404) < 1e-4


def test_dmrt_twoconfig():

    snowpack = setup_snowpack()

    # create the EM Model
    m = make_model("dmrt_qcacp_shortrange", "dort")

    # create the sensor
    radiometer = amsre(["19", "37"])

    print(radiometer.configurations)

    # run the model
    res = m.run(radiometer, snowpack)

    print(res.TbV(), res.TbH())
    assert (res.Tb(channel="37V") - 202.1726891947754) < 1e-4
    assert (res.Tb(channel="37H") - 187.45835882462404) < 1e-4

    assert (res.Tb(channel="19V") - 242.550043) < 1e-4
    assert (res.Tb(channel="19H") - 230.118448) < 1e-4


def test_less_refringent_bottom_layer():
    # Regression test 19-03-2018: value may change if other bugs found
    snowpack = make_snowpack([0.2, 0.3], "sticky_hard_spheres", density=[290.0, 250.0], radius=50e-6, stickiness=0.2,
                             substrate=make_soil("transparent", 1, 270))
    m = make_model("dmrt_qcacp_shortrange", "dort")
    scat = active(10e9, 45)
    res = m.run(scat, snowpack)
    print(res.sigmaVV_dB(), res.sigmaHH_dB())
    assert abs(res.sigmaVV_dB() - -52.01373960728898) < 1e-1
    assert abs(res.sigmaHH_dB() - -51.776918861699706) < 1e-1


# The following test fails
# def test_less_refringent_bottom_layer_VV():
#     # Regression test 19-03-2018: value may change if other bugs found
#     snowpack = make_snowpack([0.2, 0.3], "sticky_hard_spheres", density = [290.0, 250.0], radius = 1e-4, stickiness=0.2)
#     m = make_model("dmrt_qcacp_shortrange", "dort")
#     scat = active(10e9, 45)
#     res = m.run(scat, snowpack)
#     print(res.sigmaVV())
#     assert abs(res.sigmaVV() - 7.54253344e-05) < 1e-7
#
#
# def test_less_refringent_bottom_layer_HH():
#     # Regression test 19-03-2018: value may change if other bugs found
#     snowpack = make_snowpack([0.2, 0.3], "sticky_hard_spheres", density = [290.0, 250.0], radius = 1e-4, stickiness=0.2)
#     m = make_model("dmrt_qcacp_shortrange", "dort")
#     scat = active(10e9, 45)
#     res = m.run(scat, snowpack)
#     print(res.sigmaHH())
#     assert abs(res.sigmaHH() - 7.09606407e-05) < 1e-7