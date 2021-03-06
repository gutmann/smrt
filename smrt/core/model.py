
"""A model in SMRT is composed of the electromagnetic scattering theory (:py:mod:`smrt.emmodel`) and 
the radiative transfer solver (:py:mod:`smrt.rtsolver`).
The :py:mod:`smrt.emmodel` is responsible for computation of the scattering and absorption coefficients and the phase function of a layer.
It is applied to each layer and it is even possible
to choose different emmodel for each layer (for instance for a complex medium made of different materials: snow, soil, water, atmosphere, ...).
The :py:mod:`smrt.rtsolver` is responsible for propagation of the incident or emitted energy through the layers, up to the surface, and eventually 
through the atmosphere.

To build a model, use the :py:meth:`make_model` function with the type of emmodel and type of rtsolver as arguments.
Then call the :py:meth:`Model.run` method of the model instance by specifying the sensor (:py:class:`smrt.core.sensor.Sensor`),
snowpack (:py:class:`smrt.core.snowpack.Snowpack`) and optionally atmosphere (see :py:mod:`smrt.atmosphere`).
The results are returned as a :py:class:`~smrt.core.result.Result` which can then been interrogated to retrieve brightness temperature,
backscattering coefficient, etc.

Example::

    m = make_model("iba", "rtsolver")

    result = m.run(sensor, snowpack)  # sensor and snowpack are created before

    print(result.TbV())

The :py:meth:`~Model.run` method can be used with list of snowpacks. In this case, it is recommended to set the snowpack_dimension_name and 
snowpack_dimension_values variable which gives the name and values of the coordinates that are create for the Results. This is useful with
timeseries for instance.

Example::

    snowpacks = []
    times = []
    for file in filenames:
        #  create a snowpack for each time series
        sp = ...
        snowpacks.append(sp)
        times.append(sp)

    # now run the model

    res = m.run(sensor, snowpacks, snowpack_dimension=('time', times))

The `res` variable has now a coordinate `time` and res.TbV() returns a timeseries.

"""

from collections.abc import Sequence
import itertools
import inspect
import copy

import numpy as np
import pandas as pd

from .error import SMRTError
from .result import concat_results
from .plugin import import_class
from .sensor import SensorBase
from .sensitivity_study import SensitivityStudy
from .progressbar import Progress
from smrt.core import lib


def make_model(emmodel, rtsolver=None, emmodel_options=None, rtsolver_options=None, emmodel_kwargs=None, rtsolver_kwargs=None):
    """create a new model with a given EM model and RT solver. The model is then ready to be run using the :py:meth:`Model.run` method. This function is the privileged way
    to create models compared to class instantiation. It supports automatic import of the emmodel and rtsolver modules.

    :param emmodel: type of emmodel to use. Can be given by the name of a file/module in the emmodel directory (as a string) or a class.
    :type emmodel:  string or class or list of strings or classes. If a list is given, different models are used for the different layers of the snowpack. In this case, the size of the list must be the same as the number of layers in the snowpack.
    :param rtsolver: type of solver to use. Can be given by the name of a file/module in the rtsolver directeory (as a string) or a class.
    :type rtsolver: string or class.  Can be None when only computation of the layer electromagnetic properties is needed.
    :param emmodel_options: extra arguments to use to create emmodel instance. Valid arguments depend on the selected emmodel. It is documented in for each emmodel class.
    :type emmodel_options: dict or a list of dict. In the latter case, the size of the list must be the same as the number of layers in the snowpack.
    :param rtsolver_options: extra to use to create the rtsolver instance (see __init__ of the solver used).
    :type rtsolver_options: dict

    :returns: a model instance
    """

    if emmodel_kwargs is not None:
        raise DeprecationWarning("Use emmodel_options instead of emmodel_kwargs")
        emmodel_options = emmodel_kwargs

    if rtsolver_kwargs is not None:
        raise DeprecationWarning("Use rtsolver_options instead of rtsolver_kwargs")
        rtsolver_options = rtsolver_kwargs


    return Model(emmodel, rtsolver, emmodel_options=emmodel_options, rtsolver_options=rtsolver_options)


def get_emmodel(emmodel):
    """get a new emmodel class from the file name"""
    if isinstance(emmodel, str):
        emmodel = import_class("emmodel", emmodel)
    assert inspect.isclass(emmodel)
    return emmodel


def make_emmodel(emmodel, sensor, layer, **emmodel_options):
    """create a new emmodel instance based on the emmodel class or string
    :param emmodel: type of emmodel to use. Can be given by the name of a file/module in the emmodel directory (as a string) or a class.
    :type emmodel:  string or class or list of strings or classes. If a list is given, different models are used for the different layers of the snowpack. In this case, the size of the list must be the same as the number of layers in the snowpack.
    :param sensor: sensor to use for the calculation
    :param layer: layer to use for the calculation
"""

    # instantiate
    emmodel = get_emmodel(emmodel)  # get the class
    if not isinstance(sensor, SensorBase):
        raise SMRTError("the first argument of 'run' must be a sensor")
    return emmodel(sensor, layer, **emmodel_options)  # create a emmodele


class Model(object):
    """ This class drives the whole calculation
    """
    def __init__(self, emmodel, rtsolver, emmodel_options=None, rtsolver_options=None):
        """create a new model. It is not recommended to instantiate Model class directly. Instead use the :py:meth:`make_model` function.
        """

        # emmodel can be a single value (class or string) or an array with the same size as snowpack layers array
        if lib.is_sequence(emmodel):
            self.emmodel = [get_emmodel(em) for em in emmodel]
        else:
            self.emmodel = get_emmodel(emmodel)

        if isinstance(rtsolver, str):
            self.rtsolver = import_class('rtsolver', rtsolver)
        else:
            self.rtsolver = rtsolver

        # The implementation avoid metaclass by supplying an optional list of arguments to the emmodel and rtsolver
        # to alter the behavior the emmodel (or rtsolver)
        # this is not the most general case, but metaclass can still be used for advanced user

        self.emmodel_options = emmodel_options if emmodel_options is not None else dict()
        self.rtsolver_options = rtsolver_options if rtsolver_options is not None else dict()

    def set_rtsolver_options(self, options=None, **kwargs):
        """set the option for the rtsolver"""
        if options is not None:
            if not isinstance(options, dict):
                raise SMRTError("options must be a dict")
            self.rtsolver_options = options  # overload the options

        self.rtsolver_options.update(kwargs)  # update the options

    def set_emmodel_options(self, options=None, **kwargs):
        """set the options for the emmodel"""
        if options is not None:
            if not isinstance(options, dict):
                raise SMRTError("options must be a dict")
            self.emmodel_options = options  # overload the options

        self.emmodel_options.update(kwargs)  # update the options

    def run(self, sensor, snowpack, atmosphere=None, snowpack_dimension=None, progressbar=False, parallel_computation=False, runner=None):
        """ Run the model for the given sensor configuration and return the results

            :param sensor: sensor to use for the calculation
            :param snowpack: snowpack to use for the calculation. Can be a single snowpack, a list of snowpack, a dict of snowpack or
                a SensitivityStudy object.
            :param snowpack_dimension: name and values (as a tuple) of the dimension to create for the results when a list of snowpack
                is provided. E.g. time, point, longitude, latitude. By default the dimension is called 'snowpack' and the values are
                rom 1 to the number of snowpacks.
            :param progressbar: if True, display a progress bar during multi-snowpacks computation
            :param parallel_computation: if True, use the joblib library to run the simulation in parallel.
                Otherwise, the simulations are run sequentially. See 'runner' arguments.
            :param runner: a 'runner' is a function (or more likely a class with a __call__ method) that takes a function and a
                list/generator of simulations, executes the function on each simulation and returns a list of results.
                'parallel_computation' allows to select between two default (basic) runners (sequential and joblib).
                Use 'runner' for more advanced parallel distributed computations.
            :returns: result of the calculation(s) as a :py:class:`Results` instance
        """

        if atmosphere is not None:
            raise DeprecationWarning("The atmosphere argument of the run method is going to be depreciated."
                " Setting the 'atmosphere' with make_snowpack (and similar functions) is now the recommended way.")

        if not isinstance(sensor, SensorBase):
            raise SMRTError("the first argument of 'run' must be a sensor")

        # determine the simulations to run
        simulations, dimensions = self.prepare_simulations(sensor, snowpack, snowpack_dimension)

        # determine the runner
        if runner is None:
            if parallel_computation:
                if progressbar:
                    raise SMRTError("Parallel computation is incompatible with progressbar")
                runner = JoblibParallelRunner()
            else:
                runner = SequentialRunner(progressbar=progressbar)

        #  run all the simulations (with atmosphere as long as it is not depreciated), the results is a flat list of results
        results = runner(self.run_single_simulation, ((simul, atmosphere) for simul in simulations))

        # reshape the results with successive concatenations
        for dimension in reversed(dimensions):
            n = len(dimension[1]) if isinstance(dimension, tuple) else len(dimension)
            results = [concat_results(results[i: i + n], dimension) for i in range(0, len(results), n)]

        assert len(results) == 1
        return results[0]

    def prepare_simulations(self, sensor, snowpack, snowpack_dimension):
        # return a flat list of pairs (sensor, snowpack). Each is a unique simulation. The second returned parameter
        # is the list of (axis, values) to be used to concatenate the results.

        # determine if we have several snowpacks
        # is it a SensitivityStudy object ?
        if isinstance(snowpack, SensitivityStudy):
            snowpack_dimension = snowpack.variable, snowpack.values
            snowpack = snowpack.snowpacks.tolist()

        # or is it a dict ?
        if isinstance(snowpack, dict):
            snowpack_dimension = "snowpack", list(snowpack.keys())
            snowpack = list(snowpack.values())

        # or is it a pandas Series ?
        if isinstance(snowpack, pd.Series):
            snowpack_dimension = snowpack.index
            snowpack = snowpack.tolist()

        # or a sequence ?
        if lib.is_sequence(snowpack):
            if snowpack_dimension is None:
                snowpack_dimension = "snowpack", None
            if snowpack_dimension[1] is None:
                snowpack_dimension = snowpack_dimension[0], range(len(snowpack))

        if (snowpack_dimension is not None) and (len(snowpack) != len(snowpack_dimension[1])):
            raise SMRTError("The list of snowpacks must have the same length as the snowpack_dimension")

        if isinstance(snowpack_dimension, tuple) and not isinstance(snowpack_dimension[0], str):
            raise SMRTError("When the 'snowpack_dimension' argument is a tuple, the first argument must be a string")

        # the sensor object is split in its basic sensors (config). How deep the sensor is split depends on the
        # radiative transfer solver's broadcast capability.
        rt_solver_broadcast_capability = getattr(self.rtsolver, "_broadcast_capability", [])

        sensor_configurations = [(axis, values) for (axis, values) in sensor.configurations() if axis not in rt_solver_broadcast_capability]

        def prepare_recursive(sensor, sensor_configurations):

            if sensor_configurations:
                axis, values = sensor_configurations[0]
                for sensor_subset in sensor.iterate(axis):
                    yield from prepare_recursive(sensor_subset, sensor_configurations[1:])
            else: # we're at the end
                if lib.is_sequence(snowpack):
                    for sp in snowpack:
                        yield (sensor, sp)
                else:
                    yield (sensor, snowpack)

        simulations = prepare_recursive(sensor, sensor_configurations.copy())

        dimensions = sensor_configurations
        if snowpack_dimension is not None:
            dimensions.append(snowpack_dimension)

        return simulations, dimensions


    def run_single_simulation(self, simulation, atmosphere):
        # run a single simulation
        sensor, snowpack = simulation

        # create a list of emmodel instances (ready to run)
        emmodel_instances = list()

        if lib.is_sequence(self.emmodel):
            # check we have the same number as layer in the snowpack
            assert (len(self.emmodel) == snowpack.nlayer)
            # one different model per layer
            emmodel_list = self.emmodel
        else:
            # the same model for all layers
            emmodel_list = itertools.cycle([self.emmodel])

        for i, (emmodel, layer) in enumerate(zip(emmodel_list, snowpack.layers)):
            if isinstance(self.emmodel_options, Sequence):
                emmodel_options = self.emmodel_options[i]
            else:
                emmodel_options = self.emmodel_options
            em = make_emmodel(emmodel, sensor, layer, **emmodel_options)
            emmodel_instances.append(em)

        if self.rtsolver is not None:
            # need to create the rtsolver ?
            if inspect.isclass(self.rtsolver):
                rtsolver = self.rtsolver(**self.rtsolver_options)  # create with arguments
            else:
                if not getattr(self.rtsolver, "_reentrant", False):
                    raise SMRTError("This solver can not be used in instance mode without")
                # no use the instance as it is.
                # this instances has possible memory of the last solve... and this is INCOMPATIBLE with // computation for most solver)
                # In the future this feature should be either removed or at least restricted when the // computation will be activate.
                rtsolver = self.rtsolver

            # run the rtsolver
            result = rtsolver.solve(snowpack, emmodel_instances, sensor, snowpack.atmosphere or atmosphere)

            return result

    def run_later(self, sensor, snowpack, **kwargs):

        from .run_promise import RunPromise  # local import to avoid start time

        return RunPromise(self, sensor, snowpack, kwargs)


class SequentialRunner(object):
    """Run the simulations sequentially on a single (local) core. This is the most simple, but inefficient way to run smrt simulations."""

    def __init__(self, progressbar=False):
        pass

    def __call__(self, function, argument_list):

        return [function(*args) for args in argument_list]


class JoblibParallelRunner(object):
    """Run the simulations on the local machine using all the cores, using the joblib library."""

    def __init__(self, backend='loky', n_jobs=-1, max_numerical_threads=1):
        """Joblib is a lightweight library for embarasingly parallel task.

    :param backend: see joblib documentation. The default 'loky' is the recommended backend.
    :param n_jobs: see joblib documentation. The default is to use all the cores.
    :param max_numerical_threads: :py:func:`~smrt.core.lib.set_max_numerical_threads`. The default avoid miximing different parallelism techniques.

"""
        self.n_jobs = n_jobs
        self.backend = backend

        if max_numerical_threads > 0:
            # it is recommended to set max_numerical_threads to 1, to disable numerical libraries parallelism.
            lib.set_max_numerical_threads(max_numerical_threads)

    def __call__(self, function, argument_list):

        from joblib import Parallel, delayed

        runner = Parallel(n_jobs=self.n_jobs, backend=self.backend)  # Parallel Runner

        return runner(delayed(function)(*args) for args in argument_list)


class DaskParallelRunner(object):
    """Run the simulations using dask.distributed on a cluster. This requires some set up on the cluster
    (see the dask.distributed documentation).

    TO BE DOCUMENTED.
    """

    def __init__(self, client, chunk=10):

        if isinstance(client, str):
            from dask.distributed import Client
            self.client = Client(client)
        else:
            self.client = client
        self.chunk = chunk

    def __call__(self, function, argument_list):

        def function_with_single_numerical_threads(args):
            lib.set_max_numerical_threads(1)
            return function(*args)

        # make a bag
        argument_list = list(argument_list)
        n = self.chunk

        futures = []
        for i in range(0, len(argument_list), n):
            args = argument_list[i: i + n]
            future = self.client.map(function_with_single_numerical_threads, list(args))
            futures += future

        results = self.client.gather(futures, direct=False)

        return results

