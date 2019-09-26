"""Runs a genetic algorithm for parameter tuning on specified target objective.

Example usage:
    config = <GENERATE CONFIG OBJECT>
    ga_instance = ParameterTuningGeneticAlgorithm(config)
    ga_instance.run()
"""

import random
from typing import List

from deap import base
from deap import creator
from deap import tools
import numpy as np
import scipy.interpolate
import multiprocessing

import ga_configs
import genetic_algorithm_results
import paci_2018
import protocols
import trace
import time

class ParameterTuningGeneticAlgorithm:
    """Encapsulates state and behavior of a parameter tuning genetic algorithm.

    Attributes:
        config: A config object specifying algorithm hyperparameters.
        baseline_trace: Trace which will serve as the algorithm's target
            objective.
        secondary_baseline_trace: Trace which will, in conjunction with the
            baseline trace, serve as the algorithm's target objective.
    """

    def __init__(self, config: ga_configs.ParameterTuningConfig) -> None:
        self.config = config
        self.baseline_trace = paci_2018.generate_trace(
            tunable_parameters=config.tunable_parameters,
            protocol=config.protocol)
        self.max_error = determine_max_error_from_baseline_trace(
            baseline_trace=self.baseline_trace)

        if config.secondary_protocol:
            self.secondary_baseline_trace = paci_2018.generate_trace(
                tunable_parameters=config.tunable_parameters,
                protocol=config.secondary_protocol)
            self.secondary_max_error = determine_max_error_from_baseline_trace(
                baseline_trace=self.secondary_baseline_trace)

    def run(self) -> genetic_algorithm_results.GAResultParameterTuning:
        """Runs an instance of the genetic algorithm."""
        toolbox = self._configure_toolbox()
        population = toolbox.population(self.config.population_size)
        ga_result = genetic_algorithm_results.GAResultParameterTuning(
            config=self.config)

        print('Evaluating initial population.')
        for individual in population:
            individual.fitness.values = [toolbox.evaluate(self, individual[0])]

        # Store initial population details for result processing.
        initial_population = []
        for i in range(len(population)):
            initial_population.append(
                genetic_algorithm_results.ParameterTuningIndividual(
                    parameters=population[i][0],
                    fitness=population[i].fitness.values[0]))
        ga_result.generations.append(initial_population)

        for generation in range(1, self.config.max_generations):
            print('Generation {}'.format(generation))
            # Offspring are chosen through tournament selection. They are then
            # cloned, because they will be modified in-place later on.
            selected_offspring = toolbox.select(population, len(population))
            offspring = [toolbox.clone(i) for i in selected_offspring]

            for i_one, i_two in zip(offspring[::2], offspring[1::2]):
                if random.random() < self.config.mate_probability:
                    toolbox.mate(self, i_one, i_two)
                    del i_one.fitness.values
                    del i_two.fitness.values

            for i in offspring:
                if random.random() < self.config.mutate_probability:
                    toolbox.mutate(self, i)
                    del i.fitness.values

            # All individuals who were updated, either through crossover or
            # mutation, will be re-evaluated.
            updated_individuals = [i for i in offspring if not i.fitness.values]
            for individual in updated_individuals:
                individual.fitness.values = [
                    toolbox.evaluate(self, individual[0])
                ]

            population = offspring

            # Store intermediate population details for result processing.
            intermediate_population = []
            for i in range(len(population)):
                intermediate_population.append(
                    genetic_algorithm_results.ParameterTuningIndividual(
                        parameters=population[i][0],
                        fitness=population[i].fitness.values[0]))
            ga_result.generations.append(intermediate_population)

            generate_statistics(population)
        return ga_result

    def _evaluate_performance(self, new_parameters: List[float]=None) -> float:
        """Evaluates performance of an individual compared to the target obj.

        Args:
            new_parameters: New parameters to use in the model.

        Returns:
            The error between the trace generated by the individual's parameter
            set and the baseline target objective.
        """
        primary_trace = paci_2018.generate_trace(
            tunable_parameters=self.config.tunable_parameters,
            protocol=self.config.protocol,
            params=new_parameters)

        if not primary_trace:
            error = self.max_error
        else:
            error = _evaluate_performance_based_on_protocol(
                protocol=self.config.protocol,
                baseline_trace=self.baseline_trace,
                indiv_trace=primary_trace) / self.max_error

        if hasattr(self, 'secondary_baseline_trace'):
            secondary_trace = paci_2018.generate_trace(
                tunable_parameters=self.config.tunable_parameters,
                protocol=self.config.secondary_protocol,
                params=new_parameters)

            if not secondary_trace:
                error += self.secondary_max_error
            else:
                error += _evaluate_performance_based_on_protocol(
                    protocol=self.config.secondary_protocol,
                    baseline_trace=self.secondary_baseline_trace,
                    indiv_trace=secondary_trace) / self.secondary_max_error
        return error

    def _mate(self, i_one: List[List[float]], i_two: List[List[float]]) -> None:
        """Performs crossover between two individuals.

        There may be a possibility no parameters are swapped. This probability
        is controlled by `self.config.gene_swap_probability`. Modifies
        both individuals in-place.

        Args:
            i_one: An individual in a population.
            i_two: Another individual in the population.
        """
        for i in range(len(i_one[0])):
            if random.random() < self.config.gene_swap_probability:
                i_one[0][i], i_two[0][i] = i_two[0][i], i_one[0][i]

    def _mutate(self, individual: List[List[float]]) -> None:
        """Performs a mutation on an individual in the population.

        Chooses random parameter values from the normal distribution centered
        around each of the original parameter values. Modifies individual
        in-place.

        Args:
            individual: An individual to be mutated.
        """
        for i in range(len(individual[0])):
            if random.random() < self.config.gene_mutation_probability:
                individual[0][i] = np.random.normal(individual[0][i])

    def _initialize_parameters(self) -> List[float]:
        """Initializes random values within constraints of all tunable params.

        Returns:
            A new set of randomly generated parameter values.
        """
        # Builds a list of parameters using random upper and lower bounds.
        randomized_parameters = []
        for param in self.config.tunable_parameters:
            random_param = random.uniform(
                param.default_value * self.config.params_lower_bound,
                param.default_value * self.config.params_upper_bound)
            randomized_parameters.append(random_param)
        return randomized_parameters

    def _configure_toolbox(self) -> base.Toolbox:
        """Configures toolbox functions."""
        creator.create('FitnessMin', base.Fitness, weights=(-1.0,))
        creator.create('Individual', list, fitness=creator.FitnessMin)

        toolbox = base.Toolbox()

        toolbox.register('init_param', self._initialize_parameters)
        toolbox.register(
            'individual',
            tools.initRepeat,
            creator.Individual,
            toolbox.init_param,
            n=1)
        toolbox.register(
            'population',
            tools.initRepeat,
            list,
            toolbox.individual)
        toolbox.register(
            'evaluate', ParameterTuningGeneticAlgorithm._evaluate_performance)
        toolbox.register('select', tools.selTournament,
                         tournsize=self.config.tournament_size)
        toolbox.register('mate', ParameterTuningGeneticAlgorithm._mate)
        toolbox.register('mutate', ParameterTuningGeneticAlgorithm._mutate)
        return toolbox


def _evaluate_performance_based_on_protocol(protocol: protocols.PROTOCOL_TYPE,
                                            baseline_trace: trace.Trace,
                                            indiv_trace: trace.Trace) -> float:
    if (isinstance(protocol, protocols.SingleActionPotentialProtocol) or
            isinstance(protocol, protocols.IrregularPacingProtocol)):
        return _evaluate_performance_sap_or_ip(
            baseline_trace=baseline_trace,
            indiv_trace=indiv_trace)
    elif isinstance(protocol, protocols.VoltageClampProtocol):
        return _evaluate_performance_voltage_clamp(
            baseline_trace=baseline_trace,
            indiv_trace=indiv_trace)
    else:
        raise ValueError('Protocol not recognized.')


def _evaluate_performance_sap_or_ip(baseline_trace: trace.Trace,
                                    indiv_trace: trace.Trace) -> float:
    """Calculates error based on voltage for SAP and IP protocols."""
    error = 0
    base_interp = scipy.interpolate.interp1d(
        baseline_trace.t,
        baseline_trace.y)
    for i in range(len(indiv_trace.t)):
        error += (base_interp(indiv_trace.t[i]) - indiv_trace.y[i]) ** 2
    return error


def _evaluate_performance_voltage_clamp(baseline_trace: trace.Trace,
                                        indiv_trace: trace.Trace) -> float:
    """Calculates error based on current for voltage clamp protocol."""
    error = 0
    base_interp = scipy.interpolate.interp1d(
        baseline_trace.t,
        baseline_trace.current_response_info.get_current_summed())

    currents = indiv_trace.current_response_info.get_current_summed()
    for i in range(len(indiv_trace.t)):
        error += (base_interp(indiv_trace.t[i]) - currents[i]) ** 2
    return error


def determine_max_error_from_baseline_trace(baseline_trace: trace.Trace
                                            ) -> float:
    """Determines the max error from the baseline trace.

    Used to normalize error for comparative analysis.
    """
    max_error = 0
    for i in baseline_trace.y:
        max_error += (i - 0) ** 2
    return max_error


def generate_statistics(population: List[List[List[float]]]) -> None:
    fitness_values = [i.fitness.values[0] for i in population]
    print('  Min fitness: {}'.format(min(fitness_values)))
    print('  Max fitness: {}'.format(max(fitness_values)))
    print('  Average fitness: {}'.format(np.mean(fitness_values)))
    print('  Standard deviation: {}'.format(np.std(fitness_values)))
