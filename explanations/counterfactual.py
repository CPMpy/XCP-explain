import numpy as np

import cpmpy as cp
from cpmpy.transformations.get_variables import get_variables
from cpmpy.expressions.core import Expression

INFTY = 1000

def inverse_optimize(model:cp.Model, user_sol:dict, allowed_to_change:set, minimize=True):

    sub_problem = model.copy()

    obj = model.objective_
    assert isinstance(obj, Expression) and obj.name == "wsum"
    obj_weights, obj_vars = obj.args
    obj_vars = cp.cpm_array(obj_vars)
    wvars = cp.intvar(-INFTY, INFTY, shape=len(obj_weights))

    master_problem = cp.Model()
    for wvar,w,v in zip(wvars, obj_weights, obj_vars):
        if v not in allowed_to_change:
            master_problem += wvar == w

    if len(user_sol) != len(obj_vars):
        # neirest counterfactual explanation case, complete to full assignment of the objective vars
        um = cp.Model(model.constraints + [var == val for var, val in user_sol.items()])
        um.objective(model.objective_, minimize=minimize)
        assert um.solve()
        user_sol = {var : var.value() for var in obj_vars}

    # covert dict to array in correct order
    user_arr = [0 for _ in obj_vars]
    for key, val in user_sol.items():
        idx = next(i for i, var in enumerate(obj_vars) if str(var) == str(key))
        user_arr[idx] = val


    diff = wvars - obj_weights
    master_problem.minimize(np.linalg.norm(diff,ord=1))

    while 1:
        assert master_problem.solve() # find minimal perturbation in coefficients
        new_weights = wvars.value()
        sub_problem.objective(cp.sum(new_weights * obj_vars), minimize=minimize)
        assert sub_problem.solve()

        user_objval = (new_weights *  user_arr).sum()

        if minimize:
            # minimization problem, check if obj val of user is lowest possible with these weights
            if user_objval <= sub_problem.objective_value():
                return cp.sum(new_weights * obj_vars)
            else:
                master_problem += cp.sum(wvars * user_arr) <= cp.sum(wvars * obj_vars.value())


        else:
            # maximization problem, check if obj val of user is highest possible with these weights
            if user_objval >= sub_problem.objective_value():
                return cp.sum(new_weights * obj_vars)
            else:
                master_problem += cp.sum(wvars * user_arr) >= cp.sum(wvars * obj_vars.value())


if __name__ == "__main__":
    import cpmpy as cp

    bvars = cp.boolvar(shape=8)
    values = [5, 0, 3, 3, 7, 9, 3, 5]
    weights = [2, 4, 7, 6, 8, 8, 1, 6]

    m = cp.Model(cp.sum(bvars * weights) <= 35)
    m.maximize(cp.sum(bvars * values))

    user_sol = {bvars[1] : True, bvars[2] : True}

    allowed_changes = set([bvars[1], bvars[2]])

    print(inverse_optimize(m, user_sol, allowed_changes, minimize=False))


