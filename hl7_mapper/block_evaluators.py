
from typing import Dict, List
from .base_mappers import BlockEvaluator, ForCommands, MappingRow, BaseMapper, IfCommands
from .hl7_structure import HL7Structure
from .condition_evaluator import LogicEvaluator
from .utils import clean_up, get_cleaned_list, get_value_if_exists, is_empty
import pandas as pd

class BlockCollector(BlockEvaluator):
    def __init__(self, row, mapper):
        super().__init__(row, mapper)

    def collect_row(self, record: pd.Series):
        if self.inner_evaluator is not None:
            if not self.inner_evaluator.collect_row(record):
                self.append_row(self.inner_evaluator)
                self.inner_evaluator = None
            return True
        row = MappingRow(record)        
        command = clean_up(row.Source_Type).lower()
        if  command == ForCommands.For.value.lower():
            self.inner_evaluator = ForLoopEvaluator(row, self.mapper)
            return True
        if command == IfCommands.If.value.lower():
            self.inner_evaluator = IfBlockEvaluator(row, self.mapper)
            return True
        if command != self.end_block:
            self.append_row(row)
            return True
        else:
            return False


class IfBlockEvaluator(BlockCollector):
    def __init__(self, row: pd.Series, mapper: BaseMapper):
        super().__init__(row, mapper)
        self.blocks = {
            'if': {
                "clause": self.init_clause,
                "rows":[]
            },
            'else_ifs': [],
            'else': {
                "clause": None,
                "rows": []
            }
        }
        self.current_block = self.blocks['if']
        self.end_block = IfCommands.EndIf.value.lower()

    def append_row(self, row:MappingRow):
        command = clean_up(row.Source_Type).lower()
        if command == IfCommands.ElseIf.value.lower():
            new_else_if = {
                "clause": row,
                "rows":[]
            }
            self.blocks['else_ifs'].append(new_else_if)
            self.current_block = new_else_if
        elif command == IfCommands.Else.value.lower():
            self.current_block = self.blocks['else']
        else:
            self.current_block['rows'].append(row)

    def evaluate_if_condition(self, if_clause:MappingRow):
        evaluator = LogicEvaluator(self.mapper.source, self.mapper.variables)
        return evaluator.evaluate(if_clause)

    def update_row(self, row:MappingRow):
        if self.parent is not None:
            return self.parent.update_row(row)
        return row

    def evaluate_rows(self, rows:List[MappingRow|BlockEvaluator]):
        for row in rows:
            if isinstance(row, BlockEvaluator):
                row.evaluate(self)
            else:
                self.mapper.map_item(self.update_row(row))
    
    def parse_if_block(self, block:Dict[str, MappingRow|List[MappingRow]]):
        if self.evaluate_if_condition(self.update_row(block['clause'])):
            self.evaluate_rows(block['rows'])
            return True
        return False

    def evaluate(self, parent=None):
        self.initialize(parent)
        if not self.parse_if_block(self.blocks['if']):
            for else_if_clause in self.blocks['else_ifs']:
                if self.parse_if_block(else_if_clause):
                    return
        self.evaluate_rows(self.blocks['else']['rows'])

class ForLoopEvaluator(BlockCollector):
    def __init__(self, row: pd.Series, mapper: BaseMapper):
        super().__init__(row, mapper)
        self.end_block = ForCommands.EndFor.value.lower()
        self.rows=[]
        self.vars = {}
        
    def append_row(self, row):
        return self.rows.append(row)
        
    def get_hl7_mode(self, address):
        if clean_up(address) == '':
            return 'each-segment-header'
        components = get_cleaned_list(address, '_')
        if len(components)==1:
            header = get_cleaned_list(components[0], '.')
            if len(header)==1 or header[1] == '*':
                return 'each-segment-repetition'
        if len(components) == 2:
            if components[1] == '*':
                return 'each-field'
            fields = get_cleaned_list(components[1], '.')
            if len(fields)==1 or fields[1]=='*':
                return 'each-field-repetition'
        if len(components) == 3:
            if components[2] == '*':
                return 'each-component'
        if len(components) == 4:
            if components[3] == '*':
                return 'each-sub-component'
        raise Exception('malformed HL7 address for iteration: ' + address)
    
    def get_hl7_base(self, mode, address):
        source:HL7Structure = self.mapper.source
        if mode == 'each-segment-header':
            return source.get_source_segment_headers()
        if mode == 'each-segment-repetition':
            return source.get_source_segment_repetitions(address)
        if mode == 'each-field':
            return source.get_source_fields(address)
        else:
            return source.get_hl7_indices(address, mode)

    def setup_iteration(self, header):
        if self.vars['mode']  == 'each-hl7-item':
            self.vars['mode'] = self.get_hl7_mode(header)
            self.vars['base'] = self.get_hl7_base(self.vars['mode'], header)
        elif self.vars['mode'] == 'each':
            self.vars['base'] = self.get_array_base(header)
        self.vars['counter'] = -1
        self.vars['header'] = header

    def get_array_base(self, target:str):
        if ';;' in target:
            return[get_value_if_exists(t, self.mapper.source, self.mapper.variables) for t in get_cleaned_list(target, ';;')]
        else:
            base = get_value_if_exists(target)
            return base if isinstance(base, list) else [base]

    def parse_for_clause(self, for_clause:MappingRow):
        clause = get_cleaned_list(for_clause.Source_Location, "::")
        self.vars['mode'] = clause[0]
        self.vars['var_name'] = str(for_clause.Destination_Location).strip()
        if ';;' in self.vars['var_name']:
            components = get_cleaned_list(self.vars['var_name'],";;")
            self.vars['var_name'] = components[0]
            self.vars['var_index'] = None if len(components) ==1 else components[1]
        else:
            self.vars['var_index'] = None
        return self.setup_iteration('' if len(clause)==1 else clause[1])
        
    def update_counter(self, row:MappingRow):
        target = f"[{self.vars['var_name']}]"
        value = self.vars['base'][self.vars['counter']]
        source = clean_up(row.Source_Location).replace(target, value)
        destination = clean_up(row.Destination_Location).replace(target, value)
        if self.vars['var_index'] is not None:
            target = f"[{self.vars['var_index']}]"
            value = self.vars['counter'] + 1
            source = source.replace(target, str(value))
            destination = destination.replace(target, str(value))
        return MappingRow(pd.Series({'Source_Type': row.Source_Type,  
                   'Source_Location': source, 
                   'Destination_Type': row.Destination_Type,
                    'Destination_Location': destination
                   }))

    def update_row(self, row: MappingRow):
        if self.parent is not None:
            row = self.parent.update_row(row)
        return self.update_counter(row)            
        
    def should_continue(self):
        if self.vars['counter'] > len(self.vars['base'])-2:
            return False
        self.vars['counter'] += 1
        return True
    
    def evaluate(self, parent=None):
        self.parse_for_clause(self.initialize(parent))
        while self.should_continue():
            for row in self.rows:
                if isinstance(row, BlockEvaluator):
                    row.evaluate(self)
                else:
                    self.mapper.map_item(self.update_row(row))
