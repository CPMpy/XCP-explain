from time import time
import logging
from itertools import combinations

import random
import numpy as np

from cpmpy.transformations.get_variables import get_variables

from .datastructures import Step, DomainSet, EPSILON
from .propagate import CPPropagate, MaximalPropagate, ExactPropagate, MaximalPropagateSolveAll



def connected_network(constraints):
    """
    Returns if the constraint network is connected or not
    """
    if len(constraints) == 1:
        return True # shortcut
    scopes = {cons : set(get_variables(cons)) for cons in constraints}
    occurs_in = {var : {c for c in constraints if var in scopes[c]} for var in set().union(*scopes.values())}
    # start at a random constraint and a random variable
    to_visit = {next(iter(scopes[constraints[0]]))}
    visited = set()
    while len(to_visit):
        # try constructing path over constraint graph that visits all
        to_visit -= visited
        var = to_visit.pop()
        # else, var not visisted, add all new vars to frontier reachable from this one using any constraint
        visited.add(var)
        for cons in occurs_in[var]:
            to_visit |= scopes[cons] - visited
    return len(visited) == len(set().union(*scopes.values()))



def smallest_next_step(domains, constraints, propagator, time_limit=3600):
    """
    Computes the smallest next step given input domains and a list of constraints.
    Iterate over all subsets of constraints and check if anything can be propagated
    :param domains: a DomainSet representing the domains of variables
    :param constraints: a list of CPMpy constraints
    :param propagator: a propagator, can be maximal but not required
    :return: The smallest step in terms of constraints deriving a new literal
    """

    start_time = time()

    random.shuffle(constraints)
    candidates = []
    for size in range(1,len(constraints)+1):
        logging.info(f"Propagating constraint sets of size {size}")
        #print(f"Propagating constraint sets of size {size}")

        for i, cons in enumerate(combinations(constraints,size)):
            if time_limit - (time() - start_time) <= EPSILON:
                raise TimeoutError(f"'all_max_steps' timed out after {time() - start_time} seconds")
            if size == 2:
                if set(get_variables(cons[0])).isdisjoint(set(get_variables(cons[1]))):
                    # quick check if scopes are disjoint
                    continue # will never propagate anything new compared to single constraints
            elif not connected_network(cons):
                continue # will never propagate anything new compared to its strict subsets (which are already checked in previous iteration)

            new_domains = propagator.propagate(domains, list(cons), time_limit=time_limit -(time() - start_time))
            if new_domains == domains:
                # nothing propagated, skip
                continue
            elif new_domains < domains:
                # propagated something new, keep step
                return Step(domains, list(cons), new_domains, type="max")
            else:
                raise ValueError("The propagate domains are not a subset of the original domains!")
        if len(candidates):
            break # found all steps of smallest size, no need to test bigger steps!
    raise ValueError("Exhausted all subsets of constraints without sucessfull propagation, is the propagator maximal?")


def make_maximal(sequence, propagator):
    """
    :param sequence: a sequence of explanations steps
    :param propagator: a maximal propagator
    :return: the maximal version of the sequence
    """
    assert isinstance(propagator, MaximalPropagate), "propagator must be maximal!"

    start_time = time()

    i = 0
    D = sequence[0].Rin
    while i < len(sequence):
        step = sequence[i]
        orig_Rin, S, orig_Rout = step
        step.Rin = D
        cons_vars = get_variables(S)
        if step.Rin <= step.Rout:
            # nothing usefull propagated
            sequence.pop(i)
        elif step.type == "max" and all(orig_Rin[var] == D[var] for var in cons_vars):
            # no need to propagate
            D = DomainSet(dict(D) | {var : orig_Rout[var] for var in cons_vars})
            step.Rout = D
            i += 1
        else:
            step.Rin = D # ensure domains are correct for other vars as well
            # need to propagate
            D = propagator.propagate(D, list(S), time() - (time() - start_time))
            step.Rout = D
            step.type="max"
            i += 1

    return sequence


def construct_greedy(constraints, goal_reduction, time_limit, seed):

    start_time = time()
    random.seed(seed)
    np.random.seed(seed)

    # max_propagator = ExactPropagate(constraints=constraints, caching=False)
    # max_propagator = CPPropagate(constraints=constraints, caching=False)
    max_propagator = MaximalPropagateSolveAll(constraints=constraints, caching=True)

    domains = DomainSet({var : frozenset(range(var.lb, var.ub+1)) for var in get_variables(constraints)})
    seq = [Step(domains, [], domains, type="max", guided=False)]

    while 1:
        if time_limit - (time() - start_time) <= EPSILON:
            raise TimeoutError(f"'construct_beam' timed out after {time() - start_time} seconds")

        prev_step = seq[-1]
        _,_,domains = prev_step
        logging.info(f"{sum(len(dom) for dom in domains.values())} literals left")
        #print(f"{sum(len(dom) for dom in domains.values())} literals left")

        # find next smallest step
        next_step = smallest_next_step(domains, constraints, max_propagator, time_limit=time_limit - (time() - start_time))
        seq.append(next_step)
        if next_step.Rout <= goal_reduction:
            break

    return seq[1:] # prune first dummy state