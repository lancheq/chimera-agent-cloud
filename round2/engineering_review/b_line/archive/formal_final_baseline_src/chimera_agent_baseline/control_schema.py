"""Small llama.cpp-compatible output schemas, built only from available tools."""
from copy import deepcopy


def control_schema(field, tools, maximum):
    if field not in ('actions', 'remaining_plan') or type(maximum) is not int or not 0 <= maximum <= 32:
        raise ValueError('Invalid control schema scope')
    names = [tool.name for tool in tools]
    if not names or len(names) != len(set(names)):
        raise ValueError('Control tools must be nonempty and unique')
    branches = []
    for name in names:
        query = ({'type': 'string', 'minLength': 1, 'maxLength': 10000}
                 if name == 'search_guidelines' else {'type': 'null'})
        branches.append(dict(type='object', properties=dict(
            tool={'const': name}, query=query,
            purpose={'type': 'string', 'minLength': 1, 'maxLength': 256}),
            required=['tool', 'query', 'purpose'], additionalProperties=False))
    properties = {field: dict(type='array', items={'anyOf': branches}, minItems=0, maxItems=maximum)}
    if field == 'remaining_plan':
        properties['reason'] = dict(type='string', minLength=1, maxLength=512)
    return deepcopy(dict(type='object', properties=properties, required=list(properties), additionalProperties=False))
