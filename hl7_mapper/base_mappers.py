from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List,Any
import pandas as pd



class SuperEnum(Enum):
    @classmethod
    def values(cls):
        return [e.value for e in cls]
    @classmethod
    def keys(cls):
        return [e.name for e in cls]
    @classmethod
    def get_by_value(cls, value):
        for e in cls:
            if e.value == value:
                return e
        raise Exception(f"{value} not in class")


class MappingRow(pd.Series):
    def __init__(self, row: pd.Series):
        super().__init__(row)
        self.Source_Location = self.add_prop("Source_Location")
        self.Source_Type = self.add_prop("Source_Type")
        self.Destination_Location = self.add_prop("Destination_Location")
        self.Destination_Type = self.add_prop("Destination_Type")
    
    def add_prop(self, prop:str):
        return '' if prop not in self else self[prop]    

class MapDestination(ABC):
    sequence:List[str]
    def __init__(self):
        super().__init__()
    
    @abstractmethod
    def finalize(self):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def restart(self):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def add_destination(self,value:str,target:str):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def to_pandas_df(self, row_collection: List[Any]):
        raise Exception("Not yet Implemented")
    
class MapSource(MapDestination):
    def __init__(self):
        super().__init__()

    @abstractmethod
    def get_source_value(self, key:str):
        raise Exception("Not yet Implemented")

    @abstractmethod
    def initialize(self, row:pd.Series):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def has_component(self, key:str):
        raise Exception("Not yet Implemented")

    @abstractmethod
    def put_all(self, destination:MapDestination):
        raise Exception("Not yet Implemented")

class BaseMapper:
    source: MapSource
    destination: MapDestination
    variables: Dict[str,Any]
    @abstractmethod
    def map(self, input:pd.DataFrame) -> pd.DataFrame:
        raise Exception("Not yet Implemented")

    @abstractmethod
    def map_row(self, row: pd.Series):
        raise Exception("Not yet Implemented")
     
    @abstractmethod
    def map_item(self, map:pd.Series):
        raise Exception("Not yet Implemented")

class ForCommands(SuperEnum):
    For = 'For'
    EndFor = 'End-For'
    Break = 'Break'
    Continue = 'Continue'

class IfCommands(SuperEnum):
    If = 'If'
    ElseIf = 'Else-If'
    Else = 'Else'
    EndIf = 'End-If'

class BlockEvaluator(ABC):
    def __init__(self, row: pd.Series, mapper:BaseMapper):
        super().__init__()
        self.init_clause = MappingRow(row)
        self.mapper = mapper
        self.inner_evaluator:BlockEvaluator = None
        self.parent: BlockEvaluator = None
        self.end_block:IfCommands | ForCommands = None

    def initialize(self, parent=None):
        self.parent = parent
        return self.init_clause if parent is None else self.parent.update_row(self.init_clause)
    

    @abstractmethod
    def collect_row(self, row:MappingRow):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def append_row(self, row:MappingRow):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def evaluate(self, parent=None):
        raise Exception("Not yet Implemented")
    
    @abstractmethod
    def update_row(self):
        raise Exception("Not yet Implemented")

