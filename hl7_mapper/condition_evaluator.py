from typing import List, Any, Dict
from .base_mappers import MapSource
from .utils import (
    get_cleaned_list,
    clean_up,
    get_value_if_exists,
    is_empty
)

class StringIterator:
    def __init__(self, source:str):
        self.source = source
        self.initialize()

    def initialize(self, source=None):
        if source is None:
            source = self.source
        self.iterator = source.__iter__()
        self.current = next(self)

    def __next__(self):
        try:
            self.current = next(self.iterator)
        except StopIteration:
            self.current = None
        finally:
            return self.current
    def get_next_token(self, separator=')', iterator=None, ignore=0):
        iterator = self if iterator is None else iterator
        c = iterator.current
        if c is None or (separator=='>' and c == '<'):
            return ""        
        next(iterator)
        if c == separator:
            if ignore==0:
                return "" 
            else:
                ignore -=1
        if separator== ')' and c == '(':
                ignore += 1
        return clean_up(c) + self.get_next_token(separator, iterator, ignore)

    def toggle_separator(self, separator):
        if separator == '(' or separator == ')':
            return ')' if separator == '(' else '('
        if separator == '<' or separator == '>':
            return '>' if separator == '<' else '<'
        else:
            return separator

    def open_version(self, separator:str):
        if separator in ('(', ')'):
            return '('
        if separator in ('<','>'):
            return '<'
        return separator
    def closed_version(self, separator:str):
        if separator in ('(', ')'):
            return ')'
        if separator in ('<','>'):
            return '>'
        return separator

    def tokenize(self, separator= "(", iterator = None, include_separator=False):
        result = []
        iterator = self if iterator is None else iterator
        while iterator.current != None:
            next_clause = self.get_next_token(separator, iterator).strip()
            if next_clause == '':
                separator=self.toggle_separator(separator)
                continue
            if "(" in next_clause:
                next_clause = self.tokenize('(', StringIterator(next_clause), include_separator) 
            elif include_separator and separator == self.closed_version(separator):
                next_clause= f'{self.open_version(separator)}{next_clause}{self.closed_version(separator)}'
            result.append(next_clause)
            separator = self.toggle_separator(separator)
        return result


    def clean_output(self, output:List[str]):
        result = []
        for token in output:
            if '<' in token and token not in ["<and>", "<or>", "<not>"]:
                result[-1] = result[-1] + token[:-1]
            else:
                result.append(token)
        return result

    def normalize(self,result:List[str|List[Any]]):
        if len(result) == 0:
            return []
        if isinstance(result[0],list):
            return [self.normalize(result[0])]+ self.normalize(result[1:])
        candidate = result[0].strip()
        output = self.tokenize(separator='<', iterator = StringIterator(candidate), include_separator=True)
        output = self.clean_output(output)
        if len(output)<3 or output[0] in ('<and>', '<or>') or output[-1] in ('<and>', '<or>'):
            return output + self.normalize(result[1:])
        else:
            return [output] + self.normalize(result[1:])
    @classmethod
    def parse(cls,clause:str):
        si = StringIterator(clause)
        return si.normalize(si.tokenize('('))
        
class LogicEvaluator:
    def __init__(self, source: MapSource, vars:Dict):
        self.source= source
        self.vars = vars

    def evaluate(self,if_clause:str):
        conditions = self.find_conditions(StringIterator.parse(if_clause))
        return self.evaluate_condition(conditions)
    
    def find_conditions(self,clauses:List[str|List[Any]]):
        if len(clauses) == 0:
            return ''
        if isinstance(clauses, str):
            return clauses
        elif len(clauses) == 1:
            return self.find_conditions(clauses[0])
        else:
            op = clauses[-2]
            if op == '<not>':
                return {
                    "right": {
                        'op':op,
                        'left': self.find_conditions(clauses[-1])
                    },
                    "op": clauses[-3],
                    "left": self.find_conditions(clauses[:-3])
                }
            else:
                return {
                    "right": self.find_conditions(clauses[-1]),
                    "op": clauses[-2],
                    "left": self.find_conditions(clauses[:-2])
                }

    def evaluate_condition(self, condition:Dict[str, Any]):
        if isinstance(condition, str):
            return self.evaluate_statement(condition)
        else:
            left = self.evaluate_condition(condition['left'])
        if condition['op']=='<and>' and not left:
            return False
        if condition['op']=='<or>' and left:
            return True
        if condition['op'] =='<not>':
            return not left
        return self.evaluate_condition(condition['right'])            

    def evaluate_statement(self, statement:str):
        for op in ['==', '!=', '>', '<', '>=', '<=', ":is-in:"]:
            if op in statement:
                params = get_cleaned_list(statement, op)
                return self.evaluate_parameter(params, op)
        return self.evaluate_condition([clean_up(params)], None)

    def evaluate_is_in(self, params:List[str]):
        left = get_value_if_exists(params[0], self.source, self.vars).lower()
        right = [get_value_if_exists(p, self.source, self.vars).lower()
                 for p in get_cleaned_list(params[1], ';;')]
        return left in right

    def evaluate_parameter(self, params, op):
        if op==':is-in:':
            return self.evaluate_is_in(params)
        left = get_value_if_exists(params[0], self.source, self.vars)
        if len(params) ==1:
            return self.to_boolean(left)
        right = get_value_if_exists(params[1], self.source, self.vars)
        return self.apply_op(left, right, op)

    def to_boolean(self,param):
        return not is_empty(param) and param.lower() != 'false'
    
    def apply_op(self, left:str|List[str], right:str|List[str], op:str):
        if isinstance(left, list):
            for l in left:
                if self.apply_op(l,right, op):
                    return True
            return False
        if isinstance(right,list):
            for r in right:
                if self.apply_op(left, r, op):
                    return True
            return False
        if op == '==':
            return str(left).strip().lower() == str(right).strip().lower()
        if op == '!=':
            return str(left).strip().lower() != str(right).strip().lower()
        elif left.isdigit() and right.isdigit():
            if op == '>':
                return float(left) > float(right)
            if op == '<':
                return float(left) < float(right)
            if op == '>=':
                return float(left) >= float(right)
            if op == '<=':
                return float(left) <= float(right)

class ConditionEvaluator:
    def __init__(self, clause:str, source: MapSource, vars:Dict[str,str]):
        self.evaluator = LogicEvaluator(source, vars)
        self.clause = clause
        self.source = source
        self.vars = vars
    
    def get_phrase_list(self,source:str, target:str)->List[str]:
        s=-1
        return [(s:=source.find(target,s+1)) for _ in range(source.count(target))]

    def is_inside(self, ref:Dict, target:dict):
        return target["start"] > ref["start"] and target['end'] < ref['end']

    def search_children(self, ref:Dict, target:dict):
        for child in ref['if_children']:
            if self.is_inside(child, target):
                return self.search_children(child, target)
        return ref
    
    def add_to_children(self, root:Dict, target:Dict|int, key:str):
        if isinstance(target,int):
            t = {"start":target, "end": target+3}
        else:
            t=target
        ref = self.search_children(root,t)
        ref[key].append(target)

    def find_nested_ifs(self, if_list, endif_list):
        stack = []
        result = []
        for dt in sorted([(i,'s') for i in if_list] + [(i,'e') for i in endif_list]):
            if dt[1] == 's':
                stack.append(dt[0])
            else:
                result.append((stack.pop(), dt[0]))
        return sorted(result)

    def parse_clause(self,clause:str):
        if_list = sorted(self.get_phrase_list(clause, '<if>'))
        endif_list = sorted(self.get_phrase_list(clause, '<endif>'))
        if len(if_list) != len(endif_list):
            raise Exception(f"malformed if statement. each <if> should have a matching <endif> {clause}")
        root = None            
        for start, end in self.find_nested_ifs(if_list, endif_list):
            cur = {
                "start": start,
                "end": end,
                "else_ifs":[],
                "else":[],
                "thens":[],
                "if_children":[]
            }
            if root is None:
                root = cur
            else:
                self.add_to_children(root, cur, 'if_children')
        for target in sorted(self.get_phrase_list(clause,'<elseif>')):
            self.add_to_children(root,target, "else_ifs")
        for target in sorted(self.get_phrase_list(clause,'<else>')):
            self.add_to_children(root,target, "else")
        for target in sorted(self.get_phrase_list(clause,'<then>')):
            self.add_to_children(root,target, "thens")
        result = self.organize_clause(root, clause)
        return result
            
    def find_then_end(self, ref:Dict, index:int):
        if len(ref['else_ifs']) > index:
            return ref['else_ifs'][index]
        if len(ref['else'])>0:
            return ref['else'][0]
        return ref['end']
    
    def find_then_clause(self, ref,index,clause):
        start = ref['thens'][index] + len('<then>')
        end = self.find_then_end(ref,index)
        return self.get_clause_or_child(ref, start,end, clause)

    def get_clause_or_child(self, ref, start, end, clause):
        for child in ref['if_children']:
            if child['start'] >= start and child['end']<=end:
                return self.organize_clause(child,clause)
        return clause[start:end].strip()

    def find_else_clause(self, ref, clause):
        start = ref['else'][0] + len('<else>')
        return self.get_clause_or_child(ref,start, ref['end'], clause)


    def get_else_if_clause(self, ref:Dict, index:int, clause:str):
        then_index = ref['thens'][index+1]
        return {
            "if_clause": clause[ref['else_ifs'][index] + len('<elseif>'):then_index].strip(),
            'then_clause': self.find_then_clause(ref,index+1, clause)
        }
    
    def organize_clause(self, ref:Dict, clause:str):
        result = {}
        result['if_clause']=clause[ref['start'] + len('<if>'):ref['thens'][0]].strip()
        result['then_clause']=self.find_then_clause(ref,0,clause)
        if len(ref['else']) > 0:
            result['else_clause']=self.find_else_clause(ref,clause)
        else:
            result['else_clause'] = ''
        result['else_if_clauses'] = [
                self.get_else_if_clause(ref,i, clause)
                    for i in range(len(ref['else_ifs'])) 
            ]
        return result

    def get_clause_value(self, clause):
        if isinstance(clause,str):
            return get_value_if_exists(clause,self.source, self.vars)
        return self.evaluate(clause)
        
    def evaluate(self, clause:Dict=None):
        if clause is None:
            clause = self.parse_clause(self.clause)
        is_true = self.evaluator.evaluate(clause['if_clause'])
        if is_true:
            return self.get_clause_value(clause['then_clause'])
        for else_if_clause in clause['else_if_clauses']:
            is_true = self.evaluator.evaluate(else_if_clause['if_clause'])
            if is_true:
                return self.get_clause_value(else_if_clause['then_clause'])
        if clause['else_clause'] == '':
            return ''
        return self.get_clause_value(clause['else_clause'])

