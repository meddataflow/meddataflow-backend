
from typing import Dict, List
from datetime import datetime
import uuid
from .utils import (
    flatten_values,
    get_cleaned_list,
    get_join_candidates, 
    get_value_if_exists,
    is_empty,
    nested_actor
)
from .base_mappers import MapSource
from .condition_evaluator import ConditionEvaluator


def datetime_transform(date_value, input_format, output_format):
    if date_value == '':
        return ''
    else:
        return datetime.strptime(str(date_value), input_format).strftime(output_format)

def get_time_format(param, source, vars, default):
    p1 = flatten_values(get_value_if_exists(param, source, vars))
    return default if p1 == '' else p1

def time_transform(param:str, source:MapSource, vars: Dict[str,str], default_pattern)->str:
    params = get_cleaned_list(param, "<from>")
    date_value = flatten_values(get_value_if_exists(params[0], source,vars))
    formats = get_cleaned_list(params[1], "<to>")
    input_format = get_time_format(formats[0], source, vars, default_pattern)
    output_format = default_pattern if len(formats)== 1 else get_time_format(formats[1], source, params, default_pattern)
    candidates = get_join_candidates([date_value, input_format, output_format])
    if len(candidates) == 1:
        return datetime_transform(*candidates[0])
    return [datetime_transform(*candidate) for candidate in candidates]

def date_transform(param:str, source: MapSource, vars:Dict[str,str]):
    return time_transform(param,source, vars, "%Y-%m-%d")

def date_time_transform(param:str, source:MapSource, vars: Dict[str,str])->str:
    return time_transform(param,source, vars, "%Y-%m-%d %H:%M:%S")

def join_values(values, sep):
    candidates = get_join_candidates(values)
    if len(candidates) == 1:
        return sep.join(candidates[0])
    joined =  [sep.join(candidate) for candidate in candidates]
    return joined
        
def var_join(param:str, source:MapSource, vars: Dict[str,str])->str:
    params = get_cleaned_list(param, '<with>')
    targets = get_cleaned_list(params[0], sep=';;')
    values = [flatten_values(get_value_if_exists(target, source, vars)) for target in targets]
    if len(values) == 1 and isinstance(values[0], list):
        return join_values(values[0], params[1])
    return join_values(values, params[1])

def create_array(param:str, source:MapSource, vars: Dict[str,str])->list:
    return [get_value_if_exists(t, source, vars) for t in get_cleaned_list(param, sep=';;') if t!='']

def add_to(param:str, source:MapSource, vars: Dict[str,str])->list:
    params = get_cleaned_list(param, '<to>')
    items = [get_value_if_exists(t, source, vars) for t in get_cleaned_list(params[0], ';;') if t!='']
    array = get_value_if_exists(params[1], source, vars)
    if len(items) == 1 and str(array).isdigit():
        return int(str(array)) + int(items[0])
    return items if not isinstance(array, list) else array + items


def array_at(param:str, source:MapSource, vars: Dict[str,str])->list:
    params = get_cleaned_list(param, '<at>')
    if ';;' in params[0]:
        array = [get_value_if_exists(t) for t in get_cleaned_list(params[0], ';;') if t!='']
    else:
        array = get_value_if_exists(params[0],source, vars)
    index = ''
    if len(params) > 1:
        index = get_value_if_exists(params[1], source, vars)
    index = None if is_empty(index) else int(index)-1
    if index is not None and index < len(array):
        return array[index]
    return ''

def item_length(param:str, source:MapSource, vars: Dict[str,str])-> int:
    array = get_value_if_exists(param, source, vars)
    if ';;' in array:
        array = [get_value_if_exists(t, source, vars) for t in get_cleaned_list(array,';;') if t!='']
    return 0 if is_empty(array) else len(array)

def slice_array(param:str, source:MapSource, vars: Dict[str,str])->list:
    array_name = None
    if '<from>' in param:
        params = get_cleaned_list(param, '<from>')
        array_name = params[0]
        if '<to>' in params[1]:
            params = get_cleaned_list(params[1], '<to>')
            to_index = params[1]
            from_index = params[0]
        else:
            from_index = params[1]
            to_index = ''
    else:
        params = get_cleaned_list(params, '<to>')
        array_name = params[0]
        from_index = ''
        to_index = params[1]
    from_index = get_value_if_exists(from_index, source, vars)
    to_index = get_value_if_exists(to_index, source, vars)
    from_index = 0 if is_empty(from_index) else int(from_index)
    to_index = None if is_empty(to_index) else int(to_index)
    array = get_value_if_exists(array_name, source, vars)
    if ';;' in array:
        array = [get_value_if_exists(t, source, vars) for t in get_cleaned_list(array,';;') if t!='']
    return array[from_index-1:to_index-1]

def do_split(target, rest, source, vars):
    params2 = get_cleaned_list(rest,"<index>")
    split = get_value_if_exists(params2[0], source, vars)
    index = None
    if len(params2) >1 :
        index = get_value_if_exists(params2[1], source, vars)
        index = None if is_empty(index) else int(index)-1
    if index is None:
        return target.split(split)
    return target.split(split)[index] 
    
def var_split(param:str, source:MapSource, vars: Dict[str,str])->str:
    params = get_cleaned_list(param, '<over>')
    target = get_value_if_exists(params[0], source, vars)
    actor = lambda x: do_split(x, params[1], source, vars)
    return nested_actor(target, actor)

def get_lookup_table_value(source: MapSource, vars:Dict[str, str]):
    def get_lookup_value(var):
        return get_value_if_exists(var, source, vars)
    return get_lookup_value

def create_lookup_table(ref:str, lookup_function)-> Dict[str, str]:
    return {k.lower():lookup_function(v) for k, v in [get_cleaned_list(pair,"=>") 
                                                      for pair in get_cleaned_list(ref,';;')]}

def lookup_target(target, rest, source, vars):
    lookup_function = get_lookup_table_value(source, vars)
    lookup_table = create_lookup_table(rest, lookup_function)
    if target not in lookup_table:
        return '' if 'default' not in lookup_table else lookup_table['default']
    return lookup_table[target]


def lookup(param:str, source:MapSource, vars: Dict[str,str])->str:
    params = get_cleaned_list(param, '<map-by>')
    target = get_value_if_exists(params[0], source, vars)
    return nested_actor(target, lambda x: lookup_target(x.lower(), params[1], source, vars))

def condition(param:str, source:MapSource, vars: Dict[str,str])->str:
    evaluator = ConditionEvaluator(param, source, vars)
    return evaluator.evaluate()

def current_datetime(params:List[str], source:MapSource, vars: Dict[str,str])->str:
    return datetime.now().strftime("%Y%m%d%H%M%S")

def generate_id(params:str, source:MapSource, vars: Dict[str,str])->str:
    return str(uuid.uuid4())

def var_substring(param:str, source:MapSource, vars: Dict[str,str])->str:
    just_to = False
    params = get_cleaned_list(param, '<from>')
    if len(params) == 1:
        params = get_cleaned_list(params[0],'<to>')
        just_to = True
    target = get_value_if_exists(params[0], source, vars)
    if len(params)==1:
        return target
    if just_to and len(params)==2:
        start = 0
        end = int(get_value_if_exists(params[1], source, vars))
        return nested_actor(target, lambda x: x[start:end])
    
    locations = get_cleaned_list(params[1],"<to>")
    start = None if locations[0] == '' else int(get_value_if_exists(locations[0], source, vars))
    end = None if len(locations) == 1 else int(get_value_if_exists(locations[1], source, vars))
    if start is None:
        if end is None:
            return target
        else:
            return nested_actor(target, lambda x: x[:end])
    else:
        if end is None:
            return nested_actor(target, lambda x: x[start:])
        else:
            return nested_actor(target, lambda x: x[start:end])
        
transformations = {
    "join": var_join,
    "date_transform": date_transform,
    "date_time_transform": date_time_transform,
    "lookup": lookup,
    "condition": condition,
    "current_datetime": current_datetime,
    "generate_id": generate_id,
    "split": var_split,
    "substring": var_substring,
    "create_array": create_array,
    "add": add_to,
    "slice": slice_array,
    "index": array_at,
    "length": item_length,
}
