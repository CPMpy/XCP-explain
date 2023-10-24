import dataclasses
import re
from io import StringIO
import pandas as pd

def tag_to_data(string, tag, skip_lines=0, datatype=pd.DataFrame, *args, **kwargs):

    regex = rf'{tag}[\s\S]*?($|(?=\n\s*\n))'
    match = re.search(regex, string)

    data = "\n".join(match.group().split("\n")[skip_lines+1:])
    if datatype == pd.DataFrame:
        kwargs = {"header":0, "index_col":0} | kwargs
        df = pd.read_csv(StringIO(data), *args, **kwargs)
        return  df.rename(columns=lambda x: x.strip())
    return datatype(data, *args, **kwargs)



@dataclasses.dataclass
class SchedulingProblem:
    horizon : int = 0
    shifts: pd.DataFrame = None
    staff: pd.DataFrame = None
    days_off : pd.DataFrame = None
    shift_on : pd.DataFrame = None
    shift_off : pd.DataFrame = None
    cover : pd.DataFrame = None


def get_data(fname):
    from faker import Faker
    fake = Faker()
    fake.seed_instance(0)

    with open(fname, "r") as f:
        string = f.read()

    problem = SchedulingProblem()

    problem.horizon = tag_to_data(string, "SECTION_HORIZON", skip_lines=2, datatype=int)
    shifts = tag_to_data(string, "SECTION_SHIFTS", names=["ShiftID", "Length", "cannot follow"],
                         dtype={'ShiftID':str, 'Length':int, 'cannot follow':str})
    shifts.fillna("", inplace=True)
    shifts["cannot follow"] = shifts["cannot follow"].apply(lambda val : val.split("|"))
    problem.shifts = shifts

    staff = tag_to_data(string, "SECTION_STAFF", index_col=False)
    maxes = staff["MaxShifts"].str.split("|", expand=True)
    for col in maxes:
        cname = maxes[col].iloc[0].split("=")[0]
        column = maxes[col].apply(lambda x : x.split("=")[1])
        staff[cname] = column.astype(int)

    staff["name"] = [fake.unique.first_name() for _ in staff.index]
    problem.staff = staff

    problem.days_off = tag_to_data(string, "SECTION_DAYS_OFF", names=["EmployeeID", "DayIdx"])
    problem.days_off["DayIdx"] = problem.days_off["DayIdx"].apply(lambda val : val if isinstance(val, int)
                                                                         else [int(x) for x in val.split(",")])
    problem.shift_on = tag_to_data(string, "SECTION_SHIFT_ON_REQUESTS", index_col=False)
    problem.shift_off = tag_to_data(string, "SECTION_SHIFT_OFF_REQUESTS", index_col=False)
    problem.cover = tag_to_data(string, "SECTION_COVER", index_col=False)

    return problem





