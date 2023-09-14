from read_data import SchedulingProblem
import cpmpy as cp

class NurseSchedulingFactory:

    def __init__(self, data: SchedulingProblem):
        self.data = data
        # some helper vars
        self.n_types = len(data.shifts)
        self.n_nurses = len(data.staff)
        self.weekends = [(i - 1, i) for i in range(data.horizon) if i != 0 and (i + 1) % 7 == 0]
        self.shift_name_to_idx = {name: idx + 1 for idx, (name, _) in enumerate(data.shifts.iterrows())}
        self.idx_to_name = ["F"] + [key for key in self.shift_name_to_idx]
        self.shift_name_to_idx.update({"F": 0})
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.days = [f"{weekdays[i % 7]} {1 + (i // 7)}" for i in range(data.horizon)]
        # decision vars
        self.nurse_view = cp.intvar(0, self.n_types, shape=(self.n_nurses, data.horizon), name="roster")
        self.slack = cp.intvar(-self.n_nurses, self.n_nurses, shape=data.horizon, name="slack")

        # some visualization stuff
        self.day_off_color = "lightgreen"
        self.on_request_color = (183,119,41)  # copper-ish
        self.off_request_color = (212,175,55) # gold-ish

    def get_full_model(self):

        model = cp.Model()
        model += self.shift_rotation()
        model += self.max_shifts()
        model += self.max_minutes()
        model += self.min_minutes()
        # model += self.max_consecutive_automaton()
        model += self.max_consecutive()
        # model += self.min_consecutive_automaton()
        model += self.min_consecutive()
        model += self.weekend_shifts()
        model += self.days_off()
        # model += self.min_consecutive_off_automaton()
        model += self.min_consecutive_off()

        obj_func = self.shift_on_requests()
        obj_func += self.shift_off_requests()
        obj_func += self.cover_penalty()

        model.minimize(obj_func)

        return model, self.nurse_view

    def get_decision_model(self):

        model, vars = self.get_full_model()
        model.minimize(0)

        model += self.shift_on_request_decision()
        model += self.shift_off_request_decision()
        model += self.cover_decision()

        return model, vars

    def get_slack_model(self):

        model, vars = self.get_full_model()

        model += self.shift_on_request_decision()
        model += self.shift_off_request_decision()
        model += self.cover_slack()
        model.minimize(abs(self.slack).max())

        return model, vars, self.slack

    def shift_rotation(self):
        """
        Shifts which cannot follow the shift on the previous day.
        This constraint always assumes that the last day of the previous planning period was a day off and
            the first day of the next planning horizon is a day off.
        """
        constraints = []
        for t, (_, shift) in enumerate(self.data.shifts.iterrows()):
            cannot_follow = [self.shift_name_to_idx[name] for name in shift["cannot follow"] if name != '']
            for other_shift in cannot_follow:
                for n in range(self.n_nurses):
                    for d in range(self.data.horizon - 1):
                        cons = (self.nurse_view[n, d] == t + 1).implies(self.nurse_view[n, d + 1] != other_shift)
                        cons.set_description(
                            f"Shift {other_shift} cannot follow {cannot_follow} for {self.data.staff.iloc[n]['name']}")
                        constraints.append(cons)
        return constraints

    def max_shifts(self, nurse_idx=None, shift_id=None, max_shifts=None):
        """
        The maximum number of shifts of each type that can be assigned to each employee.
        """
        if nurse_idx is not None:
            assert shift_id is not None and max_shifts is not None
            # specific version of constraint
            n, nurse = nurse_idx, self.data.staff.iloc[nurse_idx]
            cons = cp.Count(self.nurse_view[n], shift_id) <= max_shifts
            cons.set_description(f"{nurse['name']} cannot work more than {max_shifts} shifts of type {shift_id}")
            return cons
        else:
            constraints = []
            # get all constraints for this type
            for n, nurse in self.data.staff.iterrows():
                for t, (shift_id, shift) in enumerate(self.data.shifts.iterrows()):
                    constraints.append(
                        self.max_shifts(nurse_idx=n, shift_id=self.shift_name_to_idx[shift_id],
                                        max_shifts=nurse[shift_id])
                    )
            return constraints

    def max_minutes(self, nurse_id=None, max_m=None):
        """
        The maximum amount of total time in minutes that can be assigned to each employee.
        """
        shift_length = cp.cpm_array([0] + [l for l in self.data.shifts.Length])
        if nurse_id is not None:
            assert max_m is not None
            nurse = self.data.staff.iloc[nurse_id]
            nurse_worktime = cp.sum(shift_length[t] for t in self.nurse_view[nurse_id])
            cons = nurse_worktime <= max_m
            cons.set_description(f"{nurse['name']} cannot work more than {max_m}min")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                constraints.append(self.max_minutes(nurse_id=n, max_m=nurse['MaxTotalMinutes']))
            return constraints

    def min_minutes(self, nurse_id=None, min_m=None):
        """
        The maximum amount of total time in minutes that can be assigned to each employee.
        """
        shift_length = cp.cpm_array([0] + [l for l in self.data.shifts.Length])
        if nurse_id is not None:
            assert min_m is not None
            nurse = self.data.staff.iloc[nurse_id]
            nurse_worktime = cp.sum(shift_length[t] for t in self.nurse_view[nurse_id])
            cons = nurse_worktime >= min_m
            cons.set_description(f"{nurse['name']} cannot work more than {min_m}min")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                constraints.append(self.min_minutes(nurse_id=n, min_m=nurse['MinTotalMinutes']))
            return constraints

    def max_consecutive(self, nurse_id=None, max_days=None):
        """
        The maximum number of consecutive shifts that can be worked before having a day off.
        This constraint always assumes that the last day of the previous planning period was a day off
            and the first day of the next planning period is a day off.
        """
        if nurse_id is not None:
            assert max_days is not None
            nurse = self.data.staff.iloc[nurse_id]
            constraints = []
            for i in range(self.data.horizon - max_days):
                window = self.nurse_view[nurse_id][i:i + max_days + 1]
                assert len(window) == max_days + 1
                cons = (cp.Count(window, 0) >= 1)
                cons.set_description(f"{nurse['name']} can work at most {max_days} days before having a day off")
                cons.nurse_id = nurse_id
                cons.window = range(i, i + max_days + 1)
                constraints.append(cons)
            return constraints
        else:
            # all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                max_days = nurse["MaxConsecutiveShifts"]
                constraints.append(self.max_consecutive(nurse_id=n, max_days=max_days))
            return constraints

    def max_consecutive_automaton(self, nurse_id=None, max_days=None):
        """
        The maximum number of consecutive shifts that can be worked before having a day off.
        This constraint always assumes that the last day of the previous planning period was a day off
            and the first day of the next planning period is a day off.
        """
        if nurse_id is not None:
            assert max_days is not None
            nurse = self.data.staff.iloc[nurse_id]
            trans_func = [(i, 0, 0) for i in range(max_days + 1)]  # return to day off state
            trans_func += [(i, s + 1, i + 1) for i in range(max_days) for s in range(self.n_types)]  # counting states
            cons = cp.DirectConstraint("AddAutomaton",
                                       (self.nurse_view[nurse_id],
                                        0,
                                        list(range(0, max_days + 1)),
                                        trans_func),
                                       novar=(1, 2, 3))
            cons.set_description(f"{nurse['name']} can work at most {max_days} days before having a day off")
            return cons
        else:
            # all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                max_days = nurse["MaxConsecutiveShifts"]
                constraints.append(self.max_consecutive_automaton(nurse_id=n, max_days=max_days))
            return constraints

    def min_consecutive(self, nurse_id=None, min_days=None):
        """
            The minimum number of shifts that must be worked before having a day off.
            This constraint always assumes that there are an infinite number of consecutive shifts
                assigned at the end of the previous planning period and at the start of the next planning period.
        """
        if nurse_id is not None:
            assert min_days is not None
            nurse = self.data.staff.iloc[nurse_id]
            cons = True
            nurse_shifts = self.nurse_view[nurse_id]
            for i, shift in enumerate(nurse_shifts):
                surrounding_shifts = [
                    nurse_shifts[max(i - min_days + 1 + j, 0):i + j + 1] for j in range(min_days)
                ]
                cons &= (shift != 0).implies(cp.any(cp.Count(window, 0) == 0 for window in surrounding_shifts))

            cons.set_description(f"{nurse['name']} should work at least {min_days} days before having a day off")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                min_days = nurse["MinConsecutiveShifts"]
                constraints.append(self.min_consecutive(nurse_id=n, min_days=min_days))
            return constraints

    def min_consecutive_automaton(self, nurse_id=None, min_days=None):
        """
            The minimum number of shifts that must be worked before having a day off.
            This constraint always assumes that there are an infinite number of consecutive shifts
                assigned at the end of the previous planning period and at the start of the next planning period.
        """
        if nurse_id is not None:
            assert min_days is not None
            nurse = self.data.staff.iloc[nurse_id]
            trans_func = [(0, 0, 0)]  # state 0 is day off state
            trans_func += [(i, s + 1, i + 1) for i in range(min_days) for s in range(self.n_types)]  # counting states
            trans_func += [(min_days, s + 1, min_days) for s in range(self.n_types)]  # buffer "worked enough" state
            trans_func += [(min_days, 0, 0)]  # return to day off state
            cons = cp.DirectConstraint("AddAutomaton",
                                       (self.nurse_view[nurse_id],
                                        min_days,
                                        list(range(0, min_days + 1)),
                                        trans_func),
                                       novar=(1, 2, 3))
            cons.set_description(f"{nurse['name']} should work at least {min_days} days before having a day off")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                min_days = nurse["MinConsecutiveShifts"]
                constraints.append(self.min_consecutive_automaton(nurse_id=n, min_days=min_days))
            return constraints

    def weekend_shifts(self, nurse_id=None, max_weekends=None):
        """
            Max nb of working weekends for each nurse.
            A weekend is defined as being worked if there is a shift on the Saturday or the Sunday.
        """
        if nurse_id is not None:
            assert max_weekends is not None
            # specific version of this constraint
            nurse = self.data.staff.iloc[nurse_id]
            n_weekends = 0
            for sat, sun in self.weekends:
                n_weekends += (self.nurse_view[nurse_id, sat] + self.nurse_view[nurse_id, sun]) > 0
            cons = n_weekends <= max_weekends
            cons.set_description(f"{nurse['name']} should work at most {max_weekends} weekends")
            cons.max_weekends = max_weekends
            cons.nurse_id = nurse_id
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                constraints.append(self.weekend_shifts(nurse_id=n, max_weekends=nurse["MaxWeekends"]))
            return constraints

    def days_off(self):
        constraints = []
        for n, (_, days) in enumerate(self.data.days_off.iterrows()):
            cons = cp.sum([self.nurse_view[n, days["DayIdx"]]]) == 0
            cons.set_description(f"{self.data.staff.iloc[n]['name']} has a day off on {self.days[days['DayIdx']]}")
            cons.cell = (self.data.staff.iloc[n]['name'], days["DayIdx"], self.day_off_color)
            constraints.append(cons)
        return constraints

    def min_consecutive_off(self, nurse_id=None, min_days=None):
        """
        The minimum number of consecutive days off that must be assigned before assigning a shift.
        This constraint always assumes that there are an infinite number of consecutive days off assigned
            at the end of the previous planning period and at the start of the next planning period.
        """
        if nurse_id is not None:
            assert min_days is not None
            nurse = self.data.staff.iloc[nurse_id]
            cons = True
            nurse_shifts = self.nurse_view[nurse_id]
            for i, shift in enumerate(nurse_shifts):
                surrounding_shifts = [
                    nurse_shifts[max(i - min_days + 1 + j, 0):i + j + 1] for j in range(min_days)
                ]
                cons &= (shift == 0).implies(
                    cp.any(cp.sum((s != 0) for s in window) == 0 for window in surrounding_shifts))

            cons.set_description(f"{nurse['name']} should have at least {min_days} off consecutively")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                constraints.append(self.min_consecutive_off(nurse_id=n, min_days=nurse["MinConsecutiveDaysOff"]))
            return constraints

    def min_consecutive_off_automaton(self, nurse_id=None, min_days=None):
        """
        The minimum number of consecutive days off that must be assigned before assigning a shift.
        This constraint always assumes that there are an infinite number of consecutive days off assigned
            at the end of the previous planning period and at the start of the next planning period.
        """
        if nurse_id is not None:
            assert min_days is not None
            nurse = self.data.staff.iloc[nurse_id]

            trans_func = [(0, s + 1, 0) for s in range(self.n_types)]  # state 0 is working state
            trans_func += [(i, 0, i + 1) for i in range(min_days)]  # counting states
            trans_func += [(min_days, 0, min_days)]  # "had enough free days" state
            trans_func += [(min_days, s + 1, 0) for s in range(self.n_types)]  # return to working state
            cons = cp.DirectConstraint("AddAutomaton",
                                       (self.nurse_view[nurse_id],
                                        min_days,
                                        list(range(0, min_days + 1)),
                                        trans_func),
                                       novar=(1, 2, 3))
            cons.set_description(f"{nurse['name']} should have at least {min_days} off consecutively")
            return cons
        else:
            # get all constraints of this type
            constraints = []
            for n, nurse in self.data.staff.iterrows():
                constraints.append(
                    self.min_consecutive_off_automaton(nurse_id=n, min_days=nurse["MinConsecutiveDaysOff"]))
            return constraints

    def shift_on_request_decision(self, nurse_id=None, day=None, shift=None):
        """
            The specified shift is assigned to the specified employee on the specified day
        """
        if nurse_id is not None:
            assert is_not_none(day, shift)
            expr = self.nurse_view[nurse_id, day] == shift
            expr.set_description(
                f"{self.data.staff.iloc[nurse_id]['name']} requests to work shift {self.idx_to_name[shift]} on {self.days[day]}")
            expr.cell = (self.data.staff.iloc[nurse_id]['name'], day, self.on_request_color)
            return expr
        else:
            # all constraints of this type
            constraints = []
            for _, request in self.data.shift_on.iterrows():
                n = self.data.staff.index[self.data.staff["# ID"] == request["# EmployeeID"]].tolist().pop()
                shift = self.shift_name_to_idx[request["ShiftID"]]
                constraints.append(
                    self.shift_on_request_decision(nurse_id=n, day=request["Day"], shift=shift)
                )
            return constraints

    def shift_on_requests(self, nurse_id=None, day=None, shift=None, weight=None):
        """
            If the specified shift is not assigned to the specified employee on the specified day
                then the solution's penalty is the specified weight value.
        """
        if nurse_id is not None:
            assert is_not_none(day, shift, weight)
            assignment = ~(self.nurse_view[nurse_id, day] == shift)
            assignment.set_description(
                f"{self.data.staff.iloc[nurse_id]['name']} requests to work shift {self.idx_to_name[shift]} on {self.days[day]}")
            return weight * assignment
        else:
            # sum of all requests
            penalties = []
            for _, request in self.data.shift_on.iterrows():
                n = self.data.staff.index[self.data.staff["# ID"] == request["# EmployeeID"]].tolist().pop()
                shift = self.shift_name_to_idx[request["ShiftID"]]
                penalties.append(
                    self.shift_on_requests(nurse_id=n, day=request["Day"], shift=shift, weight=request["Weight"])
                )
            return cp.sum(penalties)

    def shift_off_request_decision(self, nurse_id=None, day=None, shift=None):
        """
            The specified shift is assigned to the specified employee on the specified day
        """
        if nurse_id is not None:
            assert is_not_none(day, shift)
            expr = self.nurse_view[nurse_id, day] != shift
            expr.set_description(
                f"{self.data.staff.iloc[nurse_id]['name']} requests to not work shift {self.idx_to_name[shift]} on {self.days[day]}")
            expr.cell = (self.data.staff.iloc[nurse_id]['name'], day, self.off_request_color)
            return expr
        else:
            # all constraints of this type
            constraints = []
            for _, request in self.data.shift_off.iterrows():
                n = self.data.staff.index[self.data.staff["# ID"] == request["# EmployeeID"]].tolist().pop()
                shift = self.shift_name_to_idx[request["ShiftID"]]
                constraints.append(
                    self.shift_off_request_decision(nurse_id=n, day=request["Day"], shift=shift)
                )
            return constraints

    def shift_off_requests(self, nurse_id=None, day=None, shift=None, weight=None):
        """
            If the specified shift is assigned to the specified employee on the specified day
                then the solution's penalty is the weight value.
        """
        if nurse_id is not None:
            assert is_not_none(day, shift, weight)
            assignment = ~(self.nurse_view[nurse_id, day] != shift)
            assignment.set_description(
                f"{self.data.staff.iloc[nurse_id]['name']} requests to not work shift {self.idx_to_name[shift]} on {self.days[day]}")
            return weight * assignment
        else:
            # sum of all requests
            penalties = []
            for _, request in self.data.shift_off.iterrows():
                n = self.data.staff.index[self.data.staff["# ID"] == request["# EmployeeID"]].tolist().pop()
                shift = self.shift_name_to_idx[request["ShiftID"]]
                penalties.append(
                    self.shift_off_requests(nurse_id=n, day=request["Day"], shift=shift, weight=request["Weight"])
                )
            return penalties

    def cover_decision(self, day=None, shift=None, requirement=None):
        """
        If the required number of staff on the specified day for the specified shift is not assigned
            then it is a soft constraint violation
        For the purposes of this tutorial, we implement it as a hard constraint
        """
        if day is not None:
            assert is_not_none(shift, requirement)
            nb_nurses = cp.Count(self.nurse_view[:, day], shift)
            expr = nb_nurses == requirement
            expr.set_description(
                f"Shift {self.idx_to_name[shift]} on {self.days[day]} must be covered by {requirement} nurses out of {len(self.nurse_view[:, day])}")
            expr.cover = day
            return expr

        else:
            # all constraints of this type
            constraints = []
            for _, cover in self.data.cover.iterrows():
                constraints.append(self.cover_decision(
                    day=cover["# Day"],
                    shift=self.shift_name_to_idx[cover["ShiftID"]],
                    requirement=cover["Requirement"]
                ))
            return constraints

    def cover_slack(self, day=None, shift=None, requirement=None):
        """
        If the required number of staff on the specified day for the specified shift is not assigned
            then it is a soft constraint violation
        For the purposes of this tutorial, we implement it as a hard constraint using a slack variable
        """
        if day is not None:
            assert is_not_none(shift, requirement)
            nb_nurses = cp.Count(self.nurse_view[:, day], shift)
            expr = nb_nurses == requirement - self.slack[day]
            expr &= self.slack[day] == requirement - nb_nurses
            expr.set_description(
                f"Shift {self.idx_to_name[shift]} on {self.days[day]} must be covered by {requirement} nurses out of {len(self.nurse_view[:, day])}")
            expr.cover = day
            return expr
        else:
            # all constraints of this type
            constraints = []
            for _, cover in self.data.cover.iterrows():
                constraints.append(self.cover_slack(
                    day=cover["# Day"],
                    shift=self.shift_name_to_idx[cover["ShiftID"]],
                    requirement=cover["Requirement"]
                ))
            return constraints

    def cover_penalty(self, day=None, shift=None, requirement=None, weight_over=None, weight_under=None):
        """
        If the required number of staff on the specified day for the specified shift is not assigned
            then it is a soft constraint violation.
        If the total number assigned is more than the required number then the solution's penalty is:
            (x - requirement) * weight for over
        If the total number assigned is less than the required number then the solution's penalty is:
            (requirement - x) * weight for under
        """
        if day is not None:
            assert is_not_none(shift, requirement, weight_over, weight_under)
            nb_nurses = cp.Count(self.nurse_view[:, day], shift)
            penalty_over = weight_over * (nb_nurses - requirement)
            penalty_under = weight_under * (requirement - nb_nurses)
            # assumption: weights are positive!
            expr = cp.max([penalty_under, penalty_over])
            expr.set_description(
                f"Shift {self.idx_to_name[shift]} on {self.days[day]} must be covered by {requirement} nurses out of {len(self.nurse_view[:, day])}")
            expr.cover = day
            return expr

        else:
            penalties = []
            for _, cover in self.data.cover.iterrows():
                penalties.append(self.cover_penalty(
                    day=cover["# Day"],
                    shift=self.shift_name_to_idx[cover["ShiftID"]],
                    requirement=cover["Requirement"],
                    weight_over=cover["Weight for over"],
                    weight_under=cover["Weight for under"]
                ))
            return cp.sum(penalties)



def is_not_none(*args):
    if any(a is None for a in args):
        return False
    else:
        return True