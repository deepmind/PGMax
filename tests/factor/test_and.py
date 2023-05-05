# Copyright 2022 Intrinsic Innovation LLC.
# Copyright 2023 DeepMind Technologies Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test building factor graphs and running inference with AND factors."""

import itertools

import jax
import numpy as np
from pgmax import factor
from pgmax import fgraph
from pgmax import fgroup
from pgmax import infer
from pgmax import vgroup


# pylint: disable=invalid-name
def test_run_bp_with_ANDFactors():
  """Test building factor graphs and running inference with AND factors.

  Simultaneously test
  (1) the support of ANDFactors in a FactorGraph and their specialized
  inference for different temperatures
  (2) the support of several factor types in a FactorGraph and during
  inference
  (3) the computation of the energy in standard and debug mode

  To do so, observe that an ANDFactor can be defined as an equivalent
  EnumFactor (which list all the valid AND configurations)
  and define two equivalent FactorGraphs:
  FG1: first half of factors are defined as EnumFactors, second half are
  defined as ANDFactors
  FG2: first half of factors are defined as ANDFactors, second half are
  defined as EnumFactors

  Inference for the EnumFactors is run with pass_enum_fac_to_var_messages
  while inference for the ANDFactors is run with
  pass_logical_fac_to_var_messages

  Note: for the first seed, add all the EnumFactors to FG1 and all the
      ANDFactors to FG2
  """
  for idx in range(16):
    np.random.seed(idx)

    # Parameters
    num_factors = np.random.randint(10, 20)
    num_parents = np.random.randint(1, 10, num_factors)
    num_parents_cumsum = np.insert(np.cumsum(num_parents), 0, 0)

    # Setting the temperature
    # The efficient message updates for OR/AND factors with linear complexity
    # comes at the cost of a decrease in computational stability
    # (1) Larger factors have higher and rarer the errors
    # (2) Temperature around 0.05 have higher errors
    if idx % 4 == 0:
      # Max-product
      temperature = 0.0
      atol = 1e-5
    elif idx % 4 == 1:
      # Low temperature are a hard test for stable updates
      temperature = 0.001
      # The efficient message updates for OR/AND factors with linear complexity
      # comes at the cost of a decrease in stability for large factors
      # and low temperature
      atol = 5e-3
    elif idx % 4 == 2:
      temperature = np.random.uniform(
          low=0.1, high=factor.logical.TEMPERATURE_STABILITY_THRE
      )
      atol = 5e-3
    else:
      temperature = np.random.uniform(
          low=factor.logical.TEMPERATURE_STABILITY_THRE, high=1.0
      )
      atol = 1e-5

    # We create different variables for the 2 FactorGraphs even if
    # we could use the same variables for both graphs
    # Graph 1
    parents_variables1 = vgroup.NDVarArray(
        num_states=2, shape=(num_parents.sum(),)
    )
    children_variables1 = vgroup.NDVarArray(num_states=2, shape=(num_factors,))
    fg1 = fgraph.FactorGraph(
        variable_groups=[parents_variables1, children_variables1]
    )

    # Graph 2
    parents_variables2 = vgroup.NDVarArray(
        num_states=2, shape=(num_parents.sum(),)
    )
    children_variables2 = vgroup.NDVarArray(num_states=2, shape=(num_factors,))
    fg2 = fgraph.FactorGraph(
        variable_groups=[parents_variables2, children_variables2]
    )

    # Option 1: Define EnumFactors equivalent to the ANDFactors
    variables_for_factors1 = []
    variables_for_factors2 = []
    for factor_idx in range(num_factors):
      variables1 = []
      for idx1 in range(
          num_parents_cumsum[factor_idx], num_parents_cumsum[factor_idx + 1]
      ):
        variables1.append(parents_variables1[idx1])
      variables1 += [children_variables1[factor_idx]]
      variables_for_factors1.append(variables1)

      variables2 = []
      for idx2 in range(
          num_parents_cumsum[factor_idx], num_parents_cumsum[factor_idx + 1]
      ):
        variables2.append(parents_variables2[idx2])
      variables2 += [children_variables2[factor_idx]]
      variables_for_factors2.append(variables2)

    # Option 1: Define EnumFactors equivalent to the ANDFactors
    for factor_idx in range(num_factors):
      this_num_parents = num_parents[factor_idx]

      configs = np.array(
          list(itertools.product([0, 1], repeat=this_num_parents + 1))
      )
      # Children state is last
      valid_AND_configs = configs[
          np.logical_and(
              configs[:, :-1].sum(axis=1) < this_num_parents,
              configs[:, -1] == 0,
          )
      ]
      valid_configs = np.concatenate(
          [np.ones((1, this_num_parents + 1), dtype=int), valid_AND_configs],
          axis=0,
      )
      assert valid_configs.shape[0] == 2**this_num_parents

      if factor_idx < num_factors // 2:
        # Add the first half of factors to FactorGraph1
        enum_factor = factor.EnumFactor(
            variables=variables_for_factors1[factor_idx],
            factor_configs=valid_configs,
            log_potentials=np.zeros(valid_configs.shape[0]),
        )
        fg1.add_factors(enum_factor)
      else:
        if idx != 0:
          # Add the second half of factors to FactorGraph2
          enum_factor = factor.EnumFactor(
              variables=variables_for_factors2[factor_idx],
              factor_configs=valid_configs,
              log_potentials=np.zeros(valid_configs.shape[0]),
          )
          fg2.add_factors(enum_factor)
        else:
          # Add all the EnumFactors to FactorGraph1 for the first iter
          enum_factor = factor.EnumFactor(
              variables=variables_for_factors1[factor_idx],
              factor_configs=valid_configs,
              log_potentials=np.zeros(valid_configs.shape[0]),
          )
          fg1.add_factors(enum_factor)

    # Option 2: Define the ANDFactors
    variables_for_ANDFactors_fg1 = []
    variables_for_ANDFactors_fg2 = []

    for factor_idx in range(num_factors):
      if factor_idx < num_factors // 2:
        # Add the first half of factors to FactorGraph2
        variables_for_ANDFactors_fg2.append(variables_for_factors2[factor_idx])
      else:
        if idx != 0:
          # Add the second half of factors to FactorGraph1
          variables_for_ANDFactors_fg1.append(
              variables_for_factors1[factor_idx]
          )
        else:
          # Add all the ANDFactors to FactorGraph2 for the first iter
          variables_for_ANDFactors_fg2.append(
              variables_for_factors2[factor_idx]
          )
    if idx != 0:
      factor_group = fgroup.ANDFactorGroup(variables_for_ANDFactors_fg1)
      fg1.add_factors(factor_group)

    factor_group = fgroup.ANDFactorGroup(variables_for_ANDFactors_fg2)
    fg2.add_factors(factor_group)

    # Set up inference
    bp1 = infer.build_inferer(fg1.bp_state, backend="bp")
    bp2 = infer.build_inferer(fg2.bp_state, backend="bp")

    # Randomly initialize the evidence
    evidence_parents = jax.device_put(
        np.random.gumbel(size=(sum(num_parents), 2))
    )
    evidence_children = jax.device_put(np.random.gumbel(size=(num_factors, 2)))

    evidence_updates1 = {
        parents_variables1: evidence_parents,
        children_variables1: evidence_children,
    }
    evidence_updates2 = {
        parents_variables2: evidence_parents,
        children_variables2: evidence_children,
    }

    # Randomly initialize the messages
    ftov_msgs_updates1 = {}
    ftov_msgs_updates2 = {}

    for idx in range(num_factors):
      ftov = np.random.normal(size=(2,))
      ftov_msgs_updates1[children_variables1[idx]] = ftov
      ftov_msgs_updates2[children_variables2[idx]] = ftov

    for idx in range(num_parents_cumsum[-1]):
      ftov = np.random.normal(size=(2,))
      ftov_msgs_updates1[parents_variables1[idx]] = ftov
      ftov_msgs_updates2[parents_variables2[idx]] = ftov

    # Run BP
    bp_arrays1 = bp1.init(
        evidence_updates=evidence_updates1, ftov_msgs_updates=ftov_msgs_updates1
    )
    bp_arrays1 = bp1.run(bp_arrays1, num_iters=5, temperature=temperature)
    bp_arrays2 = bp2.init(
        evidence_updates=evidence_updates2, ftov_msgs_updates=ftov_msgs_updates2
    )
    bp_arrays2 = bp2.run(bp_arrays2, num_iters=5, temperature=temperature)

    # Get beliefs
    beliefs1 = bp1.get_beliefs(bp_arrays1)
    beliefs2 = bp2.get_beliefs(bp_arrays2)

    assert np.allclose(
        beliefs1[children_variables1], beliefs2[children_variables2], atol=atol
    )
    assert np.allclose(
        beliefs1[parents_variables1], beliefs2[parents_variables2], atol=atol
    )

    # Get the map states and compare their energies
    map_states1 = infer.decode_map_states(beliefs1)
    map_states2 = infer.decode_map_states(beliefs2)

    energy_decoding1 = infer.compute_energy(
        fg1.bp_state, bp_arrays1, map_states1
    )[0]
    energy_decoding2 = infer.compute_energy(
        fg2.bp_state, bp_arrays2, map_states2
    )[0]
    energy_decoding1_debug, var_energies1, factor_energies1 = (
        infer.compute_energy(
            fg1.bp_state, bp_arrays1, map_states1, debug_mode=True
        )
    )
    energy_decoding2_debug, var_energies2, factor_energies2 = (
        infer.compute_energy(
            fg2.bp_state, bp_arrays2, map_states2, debug_mode=True
        )
    )
    assert np.allclose(energy_decoding1, energy_decoding2, atol=atol)
    assert np.allclose(energy_decoding1, energy_decoding1_debug, atol=atol)
    assert np.allclose(energy_decoding2, energy_decoding2_debug, atol=atol)

    # Also compare the energy of all the individual variables and factors
    for child_idx in range(num_factors):
      var_energy1 = var_energies1[children_variables1[child_idx]]
      var_energy2 = var_energies2[children_variables2[child_idx]]
      assert np.allclose(var_energy1, var_energy2, atol=atol)

    for parent_idx in range(num_parents_cumsum[-1]):
      var_energy1 = var_energies1[parents_variables1[parent_idx]]
      var_energy2 = var_energies2[parents_variables2[parent_idx]]
      assert np.allclose(var_energy1, var_energy2, atol=atol)

    for factor_idx in range(num_factors):
      factor_energy1 = factor_energies1[
          frozenset(variables_for_factors1[factor_idx])
      ]
      factor_energy2 = factor_energies2[
          frozenset(variables_for_factors2[factor_idx])
      ]
      assert np.allclose(factor_energy1, factor_energy2, atol=atol)
