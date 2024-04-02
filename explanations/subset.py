
import cpmpy as cp
from cpmpy.exceptions import CPMpyException
from cpmpy.transformations.normalize import toplevel_list

import copy

def mus(soft, hard):

    # try reification of all soft constraints
    try:
        return cpmpy.tools.mus.mus(soft, hard)
    except CPMpyException:
        return cpmpy.tools.mus.mus_naive(soft, hard)

def maxsat(soft, hard=[]):

    soft = toplevel_list(soft, merge_and=False)
    assump = cp.boolvar(shape=len(soft))
    dmap = dict(zip(assump, soft))

    m = cp.Model(hard)
    m += assump.implies(soft)
    m.maximize(cp.sum(assump))

    assert m.solve()

    return [dmap[a] for a in assump if a.value()]

def mcs(soft, hard=[], solver="ortools"):

    soft = toplevel_list(soft, merge_and=False)
    assump = cp.boolvar(shape=len(soft))
    s = cp.SolverLookup.get(solver)
    s += hard
    s += assump.implies(soft)

    s.solution_hint(assump, [1]*len(assump))
    assert s.solve()

    dmap = dict(zip(assump, soft))
    mcs = _sat_grow(s, set() , dmap)
    return [dmap[a] for a in mcs]


def optimal_mcs(soft, hard=[], solver="ortools"):

    soft = toplevel_list(soft, merge_and=False)
    assump = cp.boolvar(shape=len(soft))
    s = cp.SolverLookup.get(solver)
    s += hard
    s += assump.implies(soft)

    s.maximize(cp.sum(assump))
    assert s.solve()

    dmap = dict(zip(assump, soft))
    return [dmap[a] for a in assump if a.value() is False]


def _sat_grow(solver, sat_subset, dmap):
    """
        Find a superset of "subset" which is still satisfiable, not the largest one per se.
    """
    # to_check = _greedy_grow(dmap)
    to_check = set(dmap.keys()) - sat_subset
    sat_subset = copy.copy(sat_subset)
    while len(to_check):
        test = to_check.pop()
        new_set = copy.copy(sat_subset)
        new_set.add(test)
        # solver.solution_hint(list(new_set), len(new_set)*[1])
        if solver.solve(assumptions=list(new_set)):
            # is sat, so add to sat subset
            sat_subset = {assump for assump, cons in dmap.items() if assump.value() or cons.value()}
            to_check -= sat_subset

    return set(dmap.keys()) - sat_subset


def _greedy_grow(dmap):
    """
        Very cheaply check the values of the decision variables and construct sat set from that
    """
    sat_subset = {assump for assump, cons in dmap.items() if assump.value() or cons.value()}
    return set(dmap.keys()) - sat_subset


def _corr_subsets(subset, dmap, solver, hard):
    sat_subset = {s for s in subset}
    corr_subsets = []
    vars=  list(dmap.keys())
    solver.solution_hint(vars, [1]*len(vars))
    while solver.solve(assumptions=list(sat_subset)):
        """
            Change the grow method here if wanted!
            MaxSAT grow will probably be slow but result in very small sets to hit (GOOD!)
            SAT-grow will probably be a little faster but greedily finds a small set to hit
            Greedy-grow will not do ANY solving and simply exploit the values in the current solution
        """
        # corr_subset = _maxsat_grow(sat_subset, dmap, hard=hard, time_limit=leftover(start_time, time_limit), solver_params=solver_params)
        # corr_subset = _sat_grow(solver, sat_subset, dmap, time_limit=leftover(start_time, time_limit), solver_params=solver_params)
        corr_subset = _greedy_grow(dmap)
        if len(corr_subset) == 0:
            return corr_subsets

        sat_subset |= corr_subset
        corr_subsets.append(corr_subset)
        solver.solution_hint(vars, [1] * len(vars))
    return corr_subsets

def ocus_oneof(soft, hard=[], oneof_idxes=[], weights=1, solver="ortools", hs_solver="gurobi"):

    soft = toplevel_list(soft, merge_and=False)
    assump = cp.boolvar(shape=len(soft), name="assump")
    if len(soft) == 1:
        assump = cp.cpm_array([assump])

    m = cp.Model(hard + [assump.implies(soft)])  # each assumption variable implies a candidate
    dmap = dict(zip(assump, soft))
    s = cp.SolverLookup.get(solver, m)
    assert not s.solve(assumptions=assump), "MUS: model must be UNSAT"

    # hitting set solver stuff
    hs_solver = cp.SolverLookup.get(hs_solver)
    if len(oneof_idxes):
        hs_solver += cp.sum(assump[oneof_idxes]) == 1
    hs_solver.minimize(cp.sum(weights * assump))

    while hs_solver.solve():

        subset = assump[assump.value() == 1]
        if s.solve(assumptions=subset) is True:
            # grow subset while staying satisfiable under assumptions
            for grown in _corr_subsets(subset, dmap, s, hard=hard):
                hs_solver += cp.sum(grown) >= 1
        else:
            return [dmap[a] for a in subset]

def smus(soft, hard=[], weights=1, solver="ortools", hs_solver="gurobi"):
    return ocus_oneof(soft, hard, [], weights, solver, hs_solver)

def omus(soft, hard=[], weights=1, solver="ortools", hs_solver="gurobi"):
    return ocus_oneof(soft, hard, [], weights, solver, hs_solver)
