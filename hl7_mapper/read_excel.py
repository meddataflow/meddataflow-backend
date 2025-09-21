import pandas as pd
from typing import Union, List, Any
from pathlib import Path
from .mapper import Mapper
import argparse

class ExcelMapping:
    def __init__(self, file_name:Union[Path, str]):
        self.excel_file_nae = file_name
        self.book = pd.read_excel(file_name, None)
        self.mapping_sheets = [name for name in self.book.keys() if 'mapping' in name]
        self.control_sheet = self.book.get("control_sheet")
        self.variables = {}

    def run(self, step_list:List[str]=[]):
        self.variables = {}
        if len(step_list)==0:
            steps = self.control_sheet
        else:
            steps = self.control_sheet[self.control_sheet.Step.isin(step_list)]
        for _, step in steps.iterrows():
            self.run_step(step)
    def get_protocol_and_param(self, content:str):
        protocol, param = str(content).split("://")
        return protocol.strip().lower(), param.strip()

    def run_step(self, step:pd.Series):
        protocol , action_df = self.get_action_from_step(step)
        self.source_type =  self.get_type_from_step(step.Source_Type)
        self.source_df = self.get_source_df_from_step(step)
        self.destination_type = self.get_type_from_step(step.Destination_Type)
        destination_df = self.get_destination_df(protocol, action_df)
        self.save_destination(step, destination_df)

    def save_destination(self, step: pd.Series, df: pd.DataFrame):
        protocol, param = self.get_protocol_and_param(step.Destination_Location)
        if protocol == 'mem':
            self.variables[param] = df
        elif protocol == 'file':
            df.to_csv(param, index=False)

    def get_destination_df(self, protocol:str, action_df:pd.Series):
        if protocol == 'map':
            mapper = Mapper(mapping = action_df, source_type=self.source_type, destination_type=self.destination_type)
            return mapper.map(self.source_df)

    def get_source_df_from_step(self, step: pd.Series):
        protocol, param = self.get_protocol_and_param(step.Source_Location)
        if protocol == 'file':
            if self.source_type.upper() == 'CSV':
                return pd.read_csv(param)
        if protocol == 'mem':
            return self.variables[param] 

    def get_type_from_step(self, content:Any):
        return str(content).strip().upper()

    def get_action_from_step(self,step: pd.Series):
        protocol, param = self.get_protocol_and_param(step.Source)
        if protocol == 'map':
            return protocol , self.get_mapping_df(param)
        

    def get_mapping_df(self, param: str) -> pd.DataFrame | None:
        if param not in self.mapping_sheets:
            raise Exception(f"sheet {param} not found in mapping sheets")
        return self.book.get(param)


    def map(self, input_file:str, mapping:str):
        if mapping not in self.mapping_sheets:
            raise Exception(f"can't find sheet named {mapping}")
        mapper = Mapper(mapping=self.book.get(mapping), source_type='CSV', destination_type='HL7')
        return mapper.map(pd.read_csv(input_file))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Use an Excel workbook to map csv files to HL7 messages"
    )
    parser.add_argument("mappings", help="Excel file to use for mapping")
    parser.add_argument("sheet", help="Name of sheet with mapping information")
    parser.add_argument("input", help="input csv file to parse")
    args = parser.parse_args()
    mapper = ExcelMapping(args.mappings)
    for map in mapper.map(args.input,args.sheet):
        print(map)


