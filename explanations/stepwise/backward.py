import copy
from time import time
import logging

import cpmpy as cp
from cpmpy.tools.mus import mus
from cpmpy.transformations.get_variables import get_variables
from cpmpy.transformations.normalize import toplevel_list

from .datastructures import DomainSet, EPSILON
from .propagate import MaximalPropagate, CPPropagate, ExactPropagate, MaximalPropagateSolveAll
from ..subset import smus


def filter_sequence(seq, goal_reduction, time_limit, propagator_class=MaximalPropagate):
    """
    Filter sequence from redundant steps.
        loops over sequence from back to front and attempts to leave out a step
        if the remaining sequence is still valid, it is removed, otherwise the step is kept in the sequence
    """
    seq = copy.deepcopy(seq)

    start_time = time()

    constraints = set().union(*[set(step.S) for step in seq])
    propagator = propagator_class(list(constraints), caching=True)
    cp_propagator = CPPropagate(list(constraints), caching=True)

    conflict_cache = dict()
    def _has_conflict(Rin, seq):

        cons = frozenset(toplevel_list([step.S for step in seq]))
        if cons in conflict_cache:
            cache = conflict_cache[cons]
            if any(is_unsat for dom, is_unsat in cache.items() if Rin <= dom):
                return True
            elif any(is_unsat is False for dom, is_unsat in cache.items() if Rin >= dom):
                return False
        else:
            conflict_cache[cons] = dict()

        m = cp.Model(list(cons))
        for var, dom in Rin.items():
            m += cp.Table([var], [[val] for val in dom])
        is_unsat = m.solve() is False

        conflict_cache[cons][Rin] = is_unsat
        return is_unsat


    unsat_sequences = dict()
    sat_sequences = dict()

    def _try_deletion(Rin, seq):
        # test if remaining sequence is still valid
        subsequences = dict()  # will encounter every subsequence maximum once

        D = Rin
        unsat = None
        for j, step in enumerate(seq):
            if time_limit - (time() - start_time) <= EPSILON:
                raise TimeoutError("Filtering timed out")

            str_constraints = str([S for _, S, _ in seq[j:]])
            D_vars = DomainSet({var : D[var] for var in get_variables([S for _,S,_ in seq[j:]])})
            assert str_constraints not in subsequences, "We encountered this sequence already, should not happen!"
            subsequences[str_constraints] = D_vars
            cons_vars = get_variables(step.S)

            if D <= goal_reduction:
                # we can definitely stop
                unsat = True
                break
            elif D <= step.Rin:
                # we can deduce Rout from Rin and S, so definitely from D and S
                # This holds for all remaining steps in the sequence assuming it was valid in the first place.
                # So the sequence is valid
                unsat = True
                break
            elif str_constraints in unsat_sequences and any(D_vars <= dom for dom in unsat_sequences[str_constraints]):
                # we decided this sequence ends in UNSAT with less literals, so this one definitely
                unsat = True  # should never happen as input is maximal
                break
            elif str_constraints in sat_sequences and any(D_vars >= dom for dom in sat_sequences[str_constraints]):
                # we decided this sequence ends in SAT with more literals, so this one definitely
                unsat = False
                break
            elif step.type == "max" and all(D[var] == step.Rin[var] for var in cons_vars):
                # no need to propagate, we can copy over the relevant parts of the domains from Rin to Rout
                D = DomainSet(dict(D) | {var: step.Rout[var] for var in cons_vars})
                if any(len(dom) == 0 for dom in D.values()):
                    # unsat, must make entire reduction empty
                    D = DomainSet({var : frozenset() for var in step.Rin})
                continue
            elif _has_conflict(D_vars, seq[j:]):
                # there is still a conflict left based on constraints
                # can we get there using CP-propagation?
                Dcp= copy.deepcopy(D)
                for _,Scp,_ in seq[j:]:
                    Dcp = cp_propagator.propagate(Dcp, Scp, time_limit=time_limit - (time() - start_time))
                # we can get the goal reduction using only CP-steps, so definitely using maxprop steps
                if Dcp <= goal_reduction:
                    unsat = True
                    break

                # now we have to check what we can deduce from Rin and S
                D = propagator.propagate(D, step.S, time_limit=time_limit - (time() - start_time))
            else:
                # no conflict left in constraints, definitely not in stepwise manner either
                unsat = False
                break

        if unsat is None:
            unsat = D <= goal_reduction

        dict_to_add = unsat_sequences if unsat else sat_sequences
        # store all subsequences we encountered along the way with their initial domain
        for seq, dom in subsequences.items():
            if seq in dict_to_add:
                dict_to_add[seq].add(dom)
            else:
                dict_to_add[seq] = {dom}

        return unsat

    # iterate over sequence from back to front
    i = len(seq)-1
    while i >= 0:
        # try deleting step i and check if still valid sequence
        if _try_deletion(seq[i].Rin, seq[i+1:]):
            seq.pop(i)
        i -= 1

    # now fixup all domains in the sequence
    # set input domain to given set
    seq[0].Rin = DomainSet.from_literals(seq[0].Rin.keys(), {})
    for i, step in enumerate(seq):
        step.Rout = propagator.propagate(step.Rin, step.S, time_limit=time_limit-(time() - start_time))
        if i < len(seq)-1:
            seq[i+1].Rin = step.Rout
        if step.Rout <= goal_reduction:
            return seq[:i+1]

    return seq

def relax_sequence(seq, mus_type="mus", time_limit=3600):
    """
    Minimizes input literals for each step.
    Keeps a set of literals that need to be derived, only derive those in previous steps.
    """
    seq = copy.deepcopy(seq)

    start_time = time()

    all_constraints = set().union(*[set(step.S) for step in seq])
    propagator = MaximalPropagate(constraints = list(all_constraints))

    if mus_type == "mus":
        get_mus = mus
    elif mus_type == "smus":
        get_mus = smus
    else:
        raise ValueError(f"Unknown MUS-type: {mus_type}")

    soft = list(seq[-1].Rin.literals())
    if len(soft):
        lits_in = mus(soft=list(seq[-1].Rin.literals()), hard=seq[-1].S)
        seq[-1].Rin = DomainSet.from_literals(seq[-1].Rin.keys(), lits_in)
        R = seq[-1].Rin.literals()
        i = len(seq)-2
    else:
        return seq # length of sequence = 1

    while i >= 0:
        if time_limit - (time() - start_time) <= EPSILON:
            raise TimeoutError("Relaxing sequence timed out")
        step = seq[i]
        # find the set of literals derived in this step we actually need later in the sequence
        newlits = step.Rout.literals() - step.Rin.literals()
        new_required_lits = R & newlits
        step.Rout = DomainSet.from_literals(step.Rout.keys(), new_required_lits)
        if len(new_required_lits) == 0:
            # step can be removed from sequence as no newly derived literal is required
            # Note: this case should never occur when running on non-redundant sequences!
            seq.pop(i)
        else:
            # this step derives at least one new literal needed later on in the sequence, so we have to keep it
            cons_vars = set(get_variables(step.S))
            # shrink Rin to literals related to variables in constraints
            step.Rin = DomainSet({var : dom if var in cons_vars else frozenset(range(var.lb,var.ub+1)) for var, dom in step.Rin.items()})
            soft = step.Rin.literals()
            hard = step.S + [cp.any([~lit for lit in step.Rout.literals()])]
            # an optimization to mainly use literals in input we need later on in the sequence anyway.
            # These literals are derived by a step earlier on in the sequence so we can use them here "for free".
            # Other literals in the current input of the step are also derived earlier, but may not actually be necessary
            #   and can therefore be deleted from outputs of previous steps when chosing the input for this step in a smart way.
            # Intuitively, we want R to stay as small as possible!
            soft1, hard1 = soft - R, hard + list(R & step.Rin.literals())
            if len(soft1) == 0:
                lits_in1 = []
            else:
                lits_in1 = get_mus(list(soft1), hard1)

            soft2, hard2 = soft & R, hard + lits_in1
            if len(soft2) == 0:
                lits_in2 = []
            else:
                lits_in2 = get_mus(list(soft2), hard2)

            lits_in = set(lits_in1) | set(lits_in2)
            step.Rin = DomainSet.from_literals(step.Rin.keys(), lits_in)

            step.Rout = propagator.propagate(domains=step.Rin, constraints=list(step.S), time_limit=time_limit-(time()-start_time))

            # update required literals
            R = (R - step.Rout.literals()) | step.Rin.literals()

        i -= 1
    return make_pertinent(seq)


def filter_simple(seq, time_limit=3600):
    start_time = time()

    # relax every step
    for step in seq:
        if time_limit <= EPSILON:
            raise TimeoutError("Filtering strongly redundant timed out during relaxation")
        mus_start = time()
        step.relax(mus_type="smus", solver="ortools", time_limit= time_limit - (time() - start_time))

    required = seq[-1].Rin.literals()
    i = len(seq)-2 # never delete last step
    while i >= 0:
        step = seq[i]
        newlits = step.Rout.literals() - step.Rin.literals()
        if len(newlits & required) == 0:
            # we do not use any new literal later on, so delete the step
            seq.pop(i)
        else: # we need the step
            required |= step.Rin.literals()
        i -= 1

    return seq



def make_pertinent(seq):
    derived_already = set()
    need_lits = set().union(*[step.Rin.literals() for step in seq])

    for step in seq[:-1]: # last step contains everything
        outlits = ((step.Rout.literals() - step.Rin.literals()) & need_lits) - derived_already
        step.Rout = DomainSet.from_literals(step.Rout.keys(), outlits)
        derived_already |= outlits
    return seq


def seq_is_pertinent(seq):
    derived_already = set()
    for step in seq[:-1]: # last step can derive everything
        if len(step.Rout.literals() & derived_already) or len(step.Rin.literals() & step.Rout.literals()):
            return False
        derived_already |= set(step.Rout.literals())
    return True
