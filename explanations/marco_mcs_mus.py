"""
    MARCO-style, but only for MSSes
    and knowing that ORTools can do maxsat(=grow) calls natively

    so: get a maxsat, block it down, get another, etc.
    blocking is directly on the maxsat model, so single model...
"""

from cpmpy import *
from cpmpy.transformations.normalize import toplevel_list


def do_marco(mdl, solver="ortools"):
    """
        Basic MUS/MCS enumeration, as a simple example.
        
        Warning: all constraints in 'mdl' must support reification!
        Otherwise, you will get an "Or-tools says: invalid" error.
    """
    # ensure toplevel list
    cons = toplevel_list(mdl.constraints, merge_and=False)

    sub_solver = SubsetSolver(cons, solver=solver)
    map_solver = MapSolver(len(cons), solver=solver)

    while True:
        seed = map_solver.next_seed()
        if seed is None:
            # all MUS/MSS enumerated
            return

        if sub_solver.check_subset(seed):
            MSS = sub_solver.grow(seed)
            yield ("MSS", [cons[i] for i in MSS])
            map_solver.block_down(MSS)
        else:
            seed = sub_solver.seed_from_core()
            MUS = sub_solver.shrink(seed)
            yield ("MUS", [cons[i] for i in MUS])
            map_solver.block_up(MUS)


class SubsetSolver:
    def __init__(self, constraints, solver=None, warmstart=False):
        n = len(constraints)
        self.all_n = set(range(n))  # used for complement

        # intialise indicators
        self.indicators = BoolVar(shape=n)
        self.idcache = dict((v,i) for (i,v) in enumerate(self.indicators))
        # XXX prefer to remove constraints with more variables first
        self.idpref = [1]*n #[len(get_variables(constraints[i])) for i in self.all_n]

        # make reified model
        mdl_reif = Model([ self.indicators[i].implies(con) for i,con in enumerate(constraints) ])
        self.solver = SolverLookup.get(solver, mdl_reif)

        self.warmstart = warmstart
        if warmstart:
            # for warmstarting from a previous solution
            self.user_vars = self.solver.user_vars
            self.user_vars_sol = None

    def check_subset(self, seed):
        assump = [self.indicators[i] for i in seed]
        if self.warmstart and self.user_vars_sol is not None:
            # or-tools is not incremental,
            # but we can warmstart with previous solution
            self.solver.solution_hint(self.user_vars, self.user_vars_sol)

        ret = self.solver.solve(assumptions=assump)
        if self.warmstart and ret is not False:
            # store solution for warm start
            self.user_vars_sol = [v.value() for v in self.user_vars]

        return ret

    def seed_from_core(self):
        core = self.solver.get_core()
        return set(self.idcache[v] for v in core)

    def shrink(self, seed):
        current = set(seed) # will change during loop
        # TODO: there is room for ordering the constraints here
        # E.G. by nr of variables involved...
        for i in sorted(seed, key=lambda i: self.idpref[i]):
            if i not in current:
                continue
            current.remove(i)
            if not self.check_subset(current):
                # if UNSAT, shrink to its core
                current = self.seed_from_core()
            else:
                # without 'i' its SAT, so add back
                current.add(i)
        return current

    def grow(self, seed):
        current = seed
        for i in (self.all_n).difference(seed): # complement
            current.append(i)
            if not self.check_subset(current):
                # if UNSAT, do not add in grow
                current.pop()
        return current


class MapSolver:
    def __init__(self, n, solver=None):
        """Initialization.
                Args:
               n: The number of constraints to map.
        """
        self.all_n = set(range(n))  # used for complement

        self.indicators = BoolVar(shape=n)
        # default to true for first next_seed(), "high bias"
        for v in self.indicators:
            v._value = True

        # empty model
        self.solver = SolverLookup.get(solver)

    def next_seed(self):
        """Get the seed from the current model, if there is one.
               Returns:
               A seed as an array of 0-based constraint indexes.
        """
        # try to select a lot, then it is more likely to be unsat
        self.solver.solution_hint(self.indicators, [1]*len(self.indicators))
        if self.solver.solve() is False:
            return None
        return [i for i,v in enumerate(self.indicators) if v.value()]

    def block_down(self, frompoint):
        """Block down from a given set."""
        complement = (self.all_n).difference(frompoint)
        self.solver += any(self.indicators[i] for i in complement)

    def block_up(self, frompoint):
        """Block up from a given set."""
        self.solver += any(~self.indicators[i] for i in frompoint)