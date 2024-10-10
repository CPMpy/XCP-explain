import numpy as np
import random
from frozendict import frozendict
from dataclasses import dataclass

import cpmpy as cp
from cpmpy.expressions.variables import NegBoolView, _BoolVarImpl
from cpmpy.expressions.core import Comparison
from cpmpy.transformations.get_variables import get_variables
from cpmpy.tools.mus import mus

EPSILON = 0.01


class DomainSet(frozendict):

    def __le__(self, other):
        assert self.keys() == other.keys(), "Keys of domain sets do not correspond, probably something is wrong"
        for var, vals in self.items():
            if var not in other:
                return False
            if not vals.issubset(other[var]):
                return False
        return True

    def __lt__(self, other):
        return self <= other and self != other

    def __gt__(self, other):
        return other < self

    def __ge__(self, other):
        return other <= self

    @staticmethod
    def from_vars(vars):
        return DomainSet({var: frozenset(range(var.lb, var.ub + 1)) for var in vars})

    @staticmethod
    def from_literals(vars, lits):
        # we need vars argument to ensure we have all the variables needed
        new_domset = {var: set(range(var.lb, var.ub + 1)) for var in vars}
        for lit in lits:
            if isinstance(lit, NegBoolView):
                new_domset[lit._bv].remove(1)
            elif isinstance(lit, _BoolVarImpl):
                new_domset[lit].remove(0)
            elif isinstance(lit, Comparison) and lit.name == "!=":
                var, val = lit.args
                new_domset[var].remove(val)
            else:
                raise ValueError(f"Unknown literal: {lit}")
        return DomainSet({var: frozenset(vals) for var, vals in new_domset.items()})

    def literals(self):
        lits = frozenset(
            {var != val for var, dom in self.items() for val in range(var.lb, var.ub + 1) if val not in dom})
        return lits


@dataclass
class Step:
    Rin: DomainSet
    S: list
    Rout: DomainSet
    type: str
    guided: bool = True
    prev = None
    is_relaxed: bool = False

    def __iter__(self):
        return iter([self.Rin, self.S, self.Rout])

    def get_path(self):
        if self.prev is None:
            return [self]
        return self.prev.get_path() + [self]

    def relax(self, mus_type="mus", solver="ortools", time_limit=3600):
        from .subset import smus
        if mus_type == "mus":
            get_mus = mus
        elif mus_type == "smus":
            get_mus = smus
        else:
            raise ValueError(f"Unknown MUS-type: {mus_type}")

        # shrink Rin to scope of S
        newlits = self.Rout.literals() - self.Rin.literals()
        cons_vars = set(get_variables(self.S))
        self.Rin = DomainSet(
            {var: dom if var in cons_vars else frozenset(range(var.lb, var.ub + 1)) for var, dom in self.Rin.items()})
        soft = self.Rin.literals()

        if len(soft):
            hard = self.S + [cp.any([~lit for lit in newlits])]
            lits_in = set(get_mus(list(soft), hard, solver=solver))
        else:
            lits_in = set()

        self.Rin = DomainSet.from_literals(self.Rin.keys(), lits_in)
        self.Rout = DomainSet.from_literals(self.Rin.keys(), newlits)
        self.is_relaxed = True

    def __str__(self):
        cons_vars = get_variables(self.S)
        out = "Propagated constraints:\n"
        for cons in self.S:
            out += str(cons) + "\n"
        out += "\n"
        for var in sorted(cons_vars, key=str):
            if len(self.Rin[var]) == (var.ub + 1 - var.lb) and self.Rin[var] == self.Rout[var]:
                continue
            else:
                out += f"{var}:\t"
                for val in self.Rin[var]:
                    if val in self.Rout[var]:
                        out += f"{val} "
                    else:
                        out += f"{val}\u0336 "
                out += "\n"
        return out

    def __repr__(self):
        return self.__str__()


# some small tests
if __name__ == "__main__":
    from cpmpy import *
