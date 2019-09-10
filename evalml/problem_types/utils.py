from .problem_types import ProblemTypes


def handle_problem_types(problem_types):
    """Converts str/list(str) to ProblemTypes/list(ProblemTypes)

    Args:
        problem_types (str/list(str])/ProblemTypes/list(ProblemTypes)) : path to file(s)

    Returns:
        DataFrame, Series : features and labels
    """
    if isinstance(problem_types, ProblemTypes):
        return problem_types
    if isinstance(problem_types, str):
        problem_types = [problem_types]
    types = list()
    for problem_type in problem_types:
        if isinstance(problem_type, ProblemTypes):
            types.append(problem_type)
        elif isinstance(problem_type, str):
            try:
                tp = ProblemTypes[problem_type.upper()]
            except KeyError:
                raise KeyError('Problem type \'{}\' does not exist'.format(problem_type))
            types.append(tp)
    return types
