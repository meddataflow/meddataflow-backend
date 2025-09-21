from typing import List, Dict
from colorama import Fore,Style
from .base_mappers import MapSource
import sys

def get_cleaned_list(target:str, sep= ',')->List[str]:
        return [clean_up(d) for d in remove_empty(target).split(sep)]

def highlight(value, fore = Fore.BLUE)->str:
    return fore + value + Style.RESET_ALL

def is_empty(value)->bool:
    return value is None or str(value).lower().strip() in ('null', 'nan', '','""',"''")

def remove_empty(value):
    if isinstance(value,list):
        return [clean_up(v) for v in value]
    return '' if is_empty(value) else str(value).strip()


def clean_up(values)-> str|List[str]:
    value = remove_empty(values)
    if isinstance(value,list):
        return [clean_up(v) for v in value]
    return str(value).strip().strip('"').strip("'")


def split_by_separator(value: str, separator:str, escape='\\'):
    """
        splits an HL7 value by separator, skipping over escaped ones.
        example: f1~f2, ~ => [f1,f2], f1~f2\\~f3, ~ => [f1, f2\\~f3]
    """
    escaped_sequence = escape + separator
    escaped_values = value.replace(escaped_sequence, '@<ESCAPED>@').split(separator)
    return [v.replace('@<ESCAPED>@', escaped_sequence) for v in escaped_values]


def get_value(var_name: str, source:MapSource, vars:Dict[str,str], not_found_exception=True)-> str:
    if is_empty(var_name):
        return ''
    if not source.has_component(var_name):
        if var_name not in vars:
            if not_found_exception:
                raise Exception(f"unknown parameter {var_name}")
            else:
                return '@NOT_FOUND@'
        else:
            value = vars[var_name]
    else:
        value = source.get_source_value(var_name)
    return clean_up(value)

def get_value_if_exists(var_name, source, vars):
    val = get_value(var_name, source, vars, not_found_exception=False)
    if val == '@NOT_FOUND@':
        return clean_up(var_name)
    return val

def get_index(content, accept_star=False):
    if accept_star == True and str(content).strip()=='*':
        return '*'
    return 0 if not str(content).isdigit() else int(str(content))

def valid_index(content):
    return str(content).strip() == '*' or str(content).isdigit()

def set_segment_index(base: str, index:int):
    split = base.split('_')
    seg = split[0].split('.')
    return '_'.join(['.'.join([seg[0], str(index)])] + split[1:])

def set_field_index(base: str, index:int):
    split = base.split('_')
    if len(split) == 1:
        return base
    seg = split[1].split('.')
    return '_'.join([split[0]] + ['.'.join([seg[0], str(index)])] + split[2:])

def nested_actor(target, actor):
    if isinstance(target, str):
        return actor(target)
    if len(target) == 0:
        return []
    return [nested_actor(target[0], actor)] + nested_actor(target[1:], actor)

def get_join_candidates(values):
    max_len = max([len(value) if isinstance(value, list) else 0 for value in values])
    if max_len == 0:
        return [values]
    return [[
            str(v) if not isinstance(v, list) 
            else v[len(v)-1] if len(v)<=i 
            else str(v[i]) 
            for v in values
        ] for i in range(max_len)]

def flatten_values(values):
    if not isinstance(values, list):
        return values
    result = []
    for value in values:
        if isinstance(value, list):
            result.extend(flatten_values(value))
        else:
            result.append(value)
    return result


def write_records_to_file(records, filename, mode='w'):
    with open(filename, mode) as f:
        for _, row in records.iterrows():
            f.write(row['message'] + '\n')