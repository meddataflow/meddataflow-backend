from typing import Dict
import pandas as pd
import re

from .block_evaluators import ForLoopEvaluator
from .base_mappers import BaseMapper, BlockEvaluator, ForCommands, MapSource, MapDestination, SuperEnum
from .utils import(
    clean_up,
    get_cleaned_list,
    is_empty
)
from .hl7_structure import HL7Structure, CSVStructure
from .transformers import transformations

class SpecialFormulas(SuperEnum):
    IMPORT_ALL = 'import_all'.lower()

class SpecialFormulaException(Exception):
    def __init__(self, formula, params, *args):
        super().__init__(*args)
        self.formula = formula
        self.params = params

class Mapper(BaseMapper):

    def __init__(self, mapping: pd.DataFrame, source_type='HL7', destination_type='CSV'):
        sequence = mapping[['Sequence']][~mapping.Sequence.isna()]
        self.sequence = [v.Sequence for _,v in sequence.iterrows() if not is_empty(v.Sequence)]
        self.mapping = mapping[[c for c in mapping.columns if c!='Sequence']][~mapping.Source_Type.isna()]
        self.source_type = source_type.upper()
        self.destination_type = destination_type.upper()
        self.source, self.destination = self.initialize()
        self.variables = {}
        self.special_functions = ['import_all']

    def initialize(self):
        upper_seqence = [v.upper() for v in self.sequence]
        if self.source_type == 'HL7':
            source = HL7Structure(upper_seqence)
        if self.source_type == 'CSV':
            source = CSVStructure(self.sequence)
        if self.destination_type == 'HL7':
            destination = HL7Structure(upper_seqence)
        if self.destination_type == 'CSV':
            destination = CSVStructure(self.sequence)
        return source, destination
        

    def map(self, input: pd.DataFrame)->pd.DataFrame:
        result= [self.map_row(row) for _, row in input.iterrows()]
        return self.destination.to_pandas_df(result)

    def map_row(self, row: pd.Series):
        self.destination.restart()
        self.source.initialize(row)
        evaluator:BlockEvaluator = None
        for _, v in self.mapping.iterrows():
                if evaluator is not None:
                    if not evaluator.collect_row(v):
                        evaluator.evaluate()
                        evaluator = None
                elif v.Source_Type.lower() == ForCommands.For.value.lower():
                    evaluator = ForLoopEvaluator(v, self)
                else:
                    self.map_item(v)
        return self.destination.finalize()

    def map_item(self, map:pd.Series):
        try:
            value = self.get_value_by_type(map)
            self.add_destination(value,map)
        except SpecialFormulaException as e: 
            self.execute_special_formula(e.formula, e.params)

    def get_value_by_type(self, map: pd.Series):
        source_type = clean_up(map.Source_Type)
        if source_type.upper() in ['HL7', 'CSV']:
            return self.source.get_source_value(clean_up(map.Source_Location))
        if source_type.lower() == 'literal':
            return clean_up(map.Source_Location)
        if source_type.lower() == 'formula':
            return self.execute_function(map)
        if source_type.lower() == 'script':
            return self.execute_script(map)
        if source_type.lower() == 'variable':
            var_name = clean_up(map.Source_Location).lower()
            return '' if var_name not in self.variables else self.variables[var_name]
        
    def execute_function(self, map: pd.Series):
        if (fcall := clean_up(map.Source_Location)) == '':
            return ''
        components = get_cleaned_list(fcall, '::')
        fname = components[0].lower()
        params = '' if len(components) == 1 else components[1]
        if fname in SpecialFormulas.values():
            raise SpecialFormulaException(SpecialFormulas.get_by_value(fname), params)
        if fname not in transformations:
            raise Exception(f"No transformation function by the name of {fname}")
        return transformations[fname](params, self.source, self.variables)

    def add_destination(self,value:str, map:pd.Series):
        destination_type = clean_up(map.Destination_Type)
        destinations = get_cleaned_list(map.Destination_Location, ',')
        for destination in destinations:
            if destination_type.upper() in ['HL7', 'CSV']:
                self.destination.add_destination(value,destination)
            if destination_type.lower() == 'variable':
                var_name = re.sub(r'[^0-9a-z]','_',clean_up(map.Destination_Location).lower())
                if var_name == '':
                    raise Exception(f'Bad variable name: {map.Destination_Location}')
                self.variables[var_name] = value
                
    def execute_special_formula(self, formula:SpecialFormulas, params:str):
            if formula == SpecialFormulas.IMPORT_ALL:
                if self.source_type != self.destination_type:
                    raise Exception(f"{self.source_type} not compatible with {self.destination_type} for full import")
                self.source.put_all(self.destination) 

    def execute_script(self, map:pd.Series):
        block = "\n\t".join([s for s in map.Source_Location.strip().split('\n')])
        defs = {}
        script = f"def custom_script(mapper, map):\n\t{block}\n"
        exec(script, defs, defs)
        return defs['custom_script'](self, map)
    
