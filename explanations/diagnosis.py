
import cpmpy as cp
from cpmpy.tools.explain.utils import make_assump_model
from cpmpy.transformations.get_variables import get_variables

def diagnose(soft, hard=[], solver="ortools", callback=lambda x : None):

    model, soft, assump = make_assump_model(soft, hard)
    dmap = dict(zip(assump, soft))
    s = cp.SolverLookup.get(solver, model)

    sat_subset = set(assump)
    corr_subset = []

    while s.solve(assumptions=list(sat_subset)) is False:

        # find new core
        core = set(s.get_core())

        for c in sorted(core, key= lambda a : len(get_variables(dmap[a]))):
            if c not in core:
                continue # already removed
            core.remove(c)
            if s.solve(assumptions=core) is True:
                # need constraint
                core.add(c)
            else: # UNSAT, do clause set refinement
                core = set(s.get_core())

        core = sorted(core, key=lambda a : str(dmap[a]))
        mus = [dmap[a] for a in core]
        callback(mus)
        print("Constraints in conflict:")
        for i, c in enumerate(mus):
            print(f"{i}.", c)

        print("and already removed constraints:")
        for a in corr_subset:
            print("-", dmap[a])

        user_input = input("Chose a constraint to remove (-1 for exit):")
        while len(user_input) <= 0:
            user_input = input("Chose a constraint to remove (-1 for exit):")
        idx = int(user_input)
        if idx < 0: break

        sat_subset.remove(core[idx])
        corr_subset.append(core[idx])

    return [dmap[a] for a in corr_subset]



def diagnose_optimal(soft, hard=[], weights=None, solver="ortools", hs_solver="ortools", callback=lambda x : None):

    model, soft, assump = make_assump_model(soft, hard)
    dmap = dict(zip(assump, soft))
    s = cp.SolverLookup.get(solver, model)


    if weights is None:
        weights = [1] * len(soft)
    hs_solver = cp.SolverLookup.get(hs_solver)
    hs_solver.minimize(cp.sum(weights * assump))

    sat_subset = set(assump)
    corr_subset = []

    while s.solve(assumptions=list(sat_subset)) is False:

        # find optimal MUS with OCUS
        while hs_solver.solve():

            hitting_set = [a for a in assump if a.value()]
            if s.solve(assumptions=hitting_set) is False:
                break # found UNSAT

            # else, the hitting set is SAT, now try to extend it without extra solve calls.
            # Check which other assumptions/constraints are satisfied (using c.value())
            # complement of grown subset is a correction subset
            new_corr_subset = [a for a, c in zip(assump, soft) if a.value() is False and c.value() is False]
            hs_solver += cp.sum(new_corr_subset) >= 1

            # greedily search for other corr subsets disjoint to this one
            sat_subset = list(new_corr_subset)
            while s.solve(assumptions=sat_subset) is True:
                new_corr_subset = [a for a, c in zip(assump, soft) if a.value() is False and c.value() is False]
                sat_subset += new_corr_subset  # extend sat subset with new corr subset, guaranteed to be disjoint
                hs_solver += cp.sum(new_corr_subset) >= 1  # add new corr subset to hitting set solver

        # hitting set is UNSAT, so found optimal MUS
        core = sorted(hitting_set, key=lambda a : str(dmap[a]))
        mus = [dmap[a] for a in core]
        callback(mus)
        print("Constraints in conflict:")
        for i, c in enumerate(mus):
            print(f"{i}.", c)

        print("and already removed constraints:")
        for a in corr_subset:
            print("-", dmap[a])

        user_input = input("Chose a constraint to remove (-1 for exit):")
        while len(user_input) <= 0:
            user_input = input("Chose a constraint to remove (-1 for exit):")
        idx = int(user_input)
        if idx < 0: break

        sat_subset.remove(core[idx])
        hs_solver += ~core[idx] # disable for future MUSes
        corr_subset.append(core[idx])

    return [dmap[a] for a in corr_subset]


