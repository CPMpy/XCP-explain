import copy
import time

import cpmpy as cp
from cpmpy.transformations.get_variables import get_variables
from cpmpy.transformations.normalize import toplevel_list
from cpmpy.expressions.utils import is_any_list


from .datastructures import DomainSet, EPSILON

def propagate(constraints, type="max"):
    if type == "max":
        cls = MaximalPropagateSolveAll
    elif type == "cp":
        cls = CPPropagate

    constraints = toplevel_list(constraints)
    prop = cls(constraints)
    doms = {var : frozenset(range(var.lb, var.ub+1)) for var in get_variables(constraints)}
    if type == "cp":
        return prop.propagate(doms, constraints, time_limit=1000, only_unit_propagation=False)
    return prop.propagate(doms, constraints, time_limit=1000)


class Propagator:

    def __init__(self, constraints:list, caching=True):
        # bi-level cache with level 1 = constraint(s), level 2 = domains,
        self.cache = dict() if caching else None
        self.vars = set(get_variables(constraints))
        self.scope_cache = dict()
        assert is_any_list(constraints), f"expected list but got {type(constraints)}"
        for cons in constraints:
            self.scope_cache[cons] = frozenset(get_variables(cons))


    def _probe_cache(self, domains, constraints) -> DomainSet:
        if self.cache is None: return None

        if not isinstance(constraints, list):
            constraints = [constraints]
        constraints = frozenset(constraints)
        if constraints not in self.cache:
            return None

        cons_vars = set().union(*[self.scope_cache[cons] for cons in constraints])
        cons_domains = DomainSet({var : domains[var] for var in cons_vars})
        new_domains = self.cache[constraints].get(cons_domains)
        if new_domains is not None:
            # are we in the UNSAT case?
            if any(len(dom) == 0 for dom in new_domains.values()):
                return DomainSet({var : frozenset() for var in domains})

            new_domains = {var : vals for var, vals in new_domains.items()} # make it a normal dict for now
            # copy domains of variables not in scope
            for var, orig_domain in domains.items():
                if var not in cons_vars:
                    new_domains[var] = orig_domain
            return DomainSet(new_domains)


    def _fill_cache(self, domains, constraints, new_domains):
        if self.cache is None: return None
        if not isinstance(constraints, list):
            constraints = [constraints]

        constraints = frozenset(constraints)
        if constraints not in self.cache:
            self.cache[constraints] = dict()

        cons_vars = set().union(*[self.scope_cache[cons] for cons in constraints])
        domains = DomainSet({var : domains[var] for var in cons_vars})
        new_domains = DomainSet({var : new_domains[var] for var in cons_vars})
        self.cache[constraints][domains] = new_domains


    def propagate(self, domains, constraints, time_limit):
        raise NotImplementedError(f"Propagation for propagator {type(self)} not implemented")



class CPPropagate(Propagator):

    prop_kwargs = dict(
        cp_model_probing_level = 0,
        presolve_bve_threshold = -1,
        presolve_probing_deterministic_time_limit = 0,
        presolve_blocked_clause = False,
        presolve_use_bva = False,
        max_presolve_iterations = 1,
        table_compression_level = 0,
        merge_no_overlap_work_limit = 0,
        merge_at_most_one_work_limit = 0,
        presolve_substitution_level = 0,
        presolve_inclusion_work_limit = 0,

    )

    def propagate(self, domains, constraints, time_limit, only_unit_propagation=True):

        # check cache
        cached = self._probe_cache(domains, constraints)
        if cached is not None: return cached

        # only care about domains of variables in constraints
        cons_vars = set(get_variables(constraints))

        solver = cp.SolverLookup.get("ortools")
        solver += constraints
        for var in cons_vars: # set leftover domains of vars
            solver += cp.Table([var],[[val] for val in domains[var]])

        if only_unit_propagation:
            solver.solve(stop_after_presolve=True,fill_tightened_domains_in_response=True, **self.prop_kwargs)
        else:
            solver.solve(stop_after_presolve=True, fill_tightened_domains_in_response=True)

        bounds = solver.ort_solver.ResponseProto().tightened_variables

        if len(bounds) == 0:
            # UNSAT, no propagation possible
            prop_dom = {var: set() for var in domains}

        else:
            prop_dom = dict()
            for var, orig_dom in domains.items():
                if var not in cons_vars:
                    prop_dom[var] = orig_dom  # unchanged
                    continue  # variable not in constraints

                ort_var = solver.solver_var(var)
                var_bounds = bounds[ort_var.Index()].domain

                lbs = [val for i, val in enumerate(var_bounds) if i % 2 == 0]
                ubs = [val for i, val in enumerate(var_bounds) if i % 2 == 1]

                prop_dom[var] = set()
                for lb, ub in zip(lbs, ubs):
                    prop_dom[var] |= set(range(lb, ub + 1))

        for val, dom in prop_dom.items():
            prop_dom[val] = frozenset(dom)

        # store new domains in cache
        self._fill_cache(domains, constraints, prop_dom)
        return DomainSet(prop_dom)


class MaximalPropagate(Propagator):
    """
        Maximal propagator
    """

    def __init__(self, constraints, caching=True):
        super().__init__(constraints, caching)
        self.cp_prop = CPPropagate(constraints, caching=caching)

    def propagate(self, domains, constraints, time_limit):
        start_time = time.time()

        # check cache
        cached = self._probe_cache(domains, constraints)
        if cached is not None: return cached

        # do a very quick CP-prop first so domains are tighter
        domains = self.cp_prop.propagate(domains, constraints, time_limit, only_unit_propagation=False)
        if any(len(dom) == 0 for dom in domains.values()):
            return domains # unsat


        # only care about variables in constraints
        cons_vars = set(get_variables(constraints))

        solver = cp.SolverLookup.get("ortools")
        solver += constraints
        for var in cons_vars:  # set leftover domains of vars
            solver += cp.Table([var], [[val] for val in domains[var]])

        # check if model is UNSAT
        if solver.solve() is False:
            prop_dom = {var : frozenset() for var in domains}

        else:
            to_visit = {var : {val for val in domains[var]} for var in cons_vars}
            while solver.solve():
                if time_limit - (time.time() - start_time) <= EPSILON:
                    raise TimeoutError("Maximal Propagate timed out")
                for var in cons_vars:
                    to_visit[var].discard(var.value())
                # ensure next iteration visits at least one new value for a variable
                lits = [var == val for var, vals in to_visit.items() for val in vals]
                if len(lits) == 0:
                    break
                solver += cp.any(lits)


            prop_dom = dict()
            for var, dom in domains.items():
                if var not in cons_vars: # unchanged domains
                    prop_dom[var] = frozenset(dom)
                else:
                    prop_dom[var] = frozenset(dom - to_visit[var])

        prop_dom = DomainSet(prop_dom)
        # store new domains in cache
        self._fill_cache(domains, constraints, prop_dom)
        return prop_dom


class MaximalPropagateSolveAll(MaximalPropagate):
    """ Alternative implementation of maximal propagate using OR-tools solveAll function"""

    def propagate(self, domains, constraints, time_limit):
        start_time = time.time()

        # check cache
        cached = self._probe_cache(domains, constraints)
        if cached is not None: return cached

        # do a very quick CP-prop first so domains are tighter
        cp_propped_domains = self.cp_prop.propagate(domains, constraints, time_limit, only_unit_propagation=False)
        if any(len(dom) == 0 for dom in cp_propped_domains.values()):
            return cp_propped_domains  # unsat

        # only care about variables in constraints
        cons_vars = set(get_variables(constraints))

        solver = cp.SolverLookup.get("ortools")
        solver += constraints
        for var in cons_vars:  # set leftover domains of vars
            solver += cp.Table([var], [[val] for val in cp_propped_domains[var]])


        visisted = {var : set() for var in cons_vars}
        def callback():
            for var in cons_vars:
                visisted[var].add(var.value())

        solver.solveAll(display=callback)

        prop_dom = dict()
        for var, dom in domains.items():
            if var not in cons_vars:  # unchanged domains
                prop_dom[var] = frozenset(dom)
            else:
                prop_dom[var] = frozenset(visisted[var])
        prop_dom = DomainSet(prop_dom)
        # store new domains in cache
        self._fill_cache(domains, constraints, prop_dom)
        return prop_dom

class ExactPropagate(MaximalPropagate):

    def __init__(self, constraints, caching=True):
        super().__init__(constraints, caching)
        self.solver = cp.SolverLookup.get("exact")
        # post reified constraints to solver
        self.cons_dict = dict()
        for cons in constraints:
            bv = cp.boolvar(name=f"->{cons}")
            self.cons_dict[cons] = bv
            self.solver += bv.implies(cons)

        # initialize solver and do all necesessary things in background
        self.solver.xct_solver.setOption("verbosity", "0")
        assert self.solver.solve()

        # keep info of last call
        self.last_call = {"domains": None, "constraints": None, "vars": None}

    def propagate(self, domains, constraints, time_limit):
        start_time = time.time()

        # check cache
        cached = self._probe_cache(domains, constraints)
        if cached is not None:
            return cached

        # do a very quick CP-prop first so domains are tighter
        cp_propped_domains = self.cp_prop.propagate(domains, constraints, time_limit, only_unit_propagation=False)
        if any(len(dom) == 0 for dom in cp_propped_domains.values()):
            return cp_propped_domains  # unsat

        if not is_any_list(constraints):
            constraints = [constraints]
        # cache miss, set assumptions to solver
        cons_vars = get_variables(constraints)

        if cp_propped_domains != self.last_call["domains"]:
            # domains changed, reset all assumptions (also for constraints as this is easier)
            #print("Resetting domains")
            self.solver.xct_solver.clearAssumptions()
            # set new assumptions for domains
            for var, dom in cp_propped_domains.items():
                self.solver.xct_solver.setAssumption(self.solver.solver_var(var), list(dom))

            # store domains of this call
            self.last_call["domains"] = cp_propped_domains
            self.last_call["constraints"] = None # reset all assumptions, so also the ones for the constraints
        
        # set assumptions for constraints
        if set(constraints) != self.last_call["constraints"]:
            if self.last_call["constraints"] is not None:
                old_cons_assump = self.solver.solver_vars([self.cons_dict[cons] for cons in self.last_call["constraints"]])
                # delete assumptions for previous constraints
                for xct_assump in old_cons_assump:
                    self.solver.xct_solver.clearAssumption(xct_assump)

            # set assumptions for new constraints
            new_cons_assump = self.solver.solver_vars([self.cons_dict[cons] for cons in constraints])
            for xct_assump in new_cons_assump:
                self.solver.xct_solver.setAssumption(xct_assump, [1])

            # store constraints of this call
            self.last_call["constraints"] = frozenset(constraints)

        # do the propagation
        # self.solver.xct_solver.setOption("timeout", str(int(time_limit - (time.time() - start_time))))
        time_limit = time_limit - (time.time() - start_time)
        new_domains = self.solver.xct_solver.pruneDomains(vars=self.solver.solver_vars(cons_vars), timeout=time_limit)
        if len(new_domains) == 0:
            raise TimeoutError("Exact propagate timed out")


        # are we in the unsat case?
        if any(len(dom) == 0 for dom in new_domains):
            prop_dom = {var : frozenset() for var in cp_propped_domains}

        else:
            prop_dom = {var : frozenset(new_domains[i]) for i, var in enumerate(cons_vars)}
            # make cons_vars a set
            cons_vars = set(cons_vars)
            for var, orig_dom in domains.items():
                if var not in cons_vars:
                    # unchanged domain
                    prop_dom[var] = orig_dom

        prop_dom = DomainSet(prop_dom)
        # store new domains in cache
        self._fill_cache(domains, constraints, prop_dom)

        return prop_dom




