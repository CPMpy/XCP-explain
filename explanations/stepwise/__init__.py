from .forward import construct_greedy
from .backward import relax_sequence, filter_sequence
from .datastructures import DomainSet

from cpmpy.transformations.normalize import toplevel_list
from cpmpy.transformations.get_variables import get_variables


def find_sequence(constraints):

    constraints = toplevel_list(constraints)
    unsat = DomainSet({var : frozenset() for var in get_variables(constraints)})
    seq = construct_greedy(constraints, unsat, time_limit=100, seed=0)
    print(f"Found sequence of length {len(seq)}")
    filtered = filter_sequence(seq, goal_reduction=unsat, time_limit=100)
    print(f"Filtered sequence to length {len(filtered)}")
    return relax_sequence(filtered, time_limit=100)