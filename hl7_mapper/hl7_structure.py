from __future__ import annotations
from typing import Dict, List, Any, Literal
import pandas as pd
from abc import ABC
from .utils import(
    get_cleaned_list,
    get_index,
    valid_index,
    set_field_index,
    split_by_separator,
    set_segment_index,
    clean_up
)
from .base_mappers import MapSource, MapDestination

class HL7Address(ABC):
    def __init__(self, address: str):
        """
        key is defined as SEG.n_Fn.Rn_Cn_Sn
        in which:
        SEG is the name of the segment
        n is the serial number of the segment, 0 if omitted
        Fn is the number of the field (separated by | )
        Rn is the number of the repetition. 0 if omitted (separated by ~)
        Cn is the number for component (separated by ^)
        Sn is the number for subcomponent (separated by &)
        examples:
        SEG.* means all the instances of a segment.
        SEG_4 means all the repetitions inside a field for the first instance of the segment
        SEG.2_4_3 meanse the third component of the fourth field of the second instance of the segment
        """
        super().__init__()
        self.address = address
        components = address.split('_')
        header = components[0].split('.')
        self.segment = header[0]
        self.index = 0 if len(header)==1 else get_index(header[1], accept_star=True)
        self.field = 0
        self.field_index = 0
        self.component = 0
        self.sub_component = 0        
        if len(components)>1:
            fields = components[1].split('.')
            self.field = get_index(fields[0])
            self.component = 0 if len(components)<3 else get_index(components[2])
            self.field_index = 0 if len(fields) ==1 else get_index(fields[1], accept_star=True)
            if self.component != 0 and self.field_index == 0:
                self.field_index = 1
            self.sub_component = 0 if len(components) < 4 else get_index(components[3])
    
    @classmethod
    def is_valid_address(cls, address:str):
        components = [a.strip() for a in address.split('_')]
        header = components[0].split('.')
        if len(components) > 4:
            return False
        if len(header[0]) != 3 or (len(components)==1 and len(header)==1): 
            return False
        if len(header) == 2 and not valid_index(header[1]):
            return False
        if len(header) > 2:
            return False
        if len(components)> 1:
            field = components[1].split('.')
            if len(field) > 2:
                return False
            for c in field:
                if not valid_index(c):
                    return False
        if len(components) > 2:
            for c in components[2:]:
                if not valid_index(c):
                    return False
        return True
        
class HL7Item:
    def __init__(self):
        self.value = ''
        self.sub_items: List[HL7Item] = []
        self.sub_item_separator = ''
        self.sub_item_type = HL7Item
    def __repr__(self):
        return self.__str__()
    def __str__(self):
        if len(self.sub_items) == 0:
            return self.value
        return self.sub_item_separator.join([str(c) for c in self.sub_items])
    def get_index(self, index:int|Literal['*']):
        if index == 0:
            index = 1
        if len(self.sub_items) < index:
            [self.sub_items.append(self.sub_item_type()) for _ in range(index - len(self.sub_items))]
        return self.sub_items[index-1]

    def add_value(self, value: str, target:HL7Address):
        """
                    puts a value in a component, at a location specified by address.
            the target shows the segment -> segment_index -> field -> field_repetitin -> component -> sub_component
            after the segment (which would be a header), everything else is a number and is optional
            if that number is zero, it means the value should be inserted at that level.
            the value will then get parsed to components and they get inserted in their correct place. 

        """
        if self.sub_item_index(target) == '*':
            if isinstance(value, list):
                for index, instance in enumerate(value):
                    self.add_value(instance, HL7Address(set_field_index(target.address, index+1)))
                   
        elif self.sub_item_index(target) == 0:
            for index , section in enumerate(split_by_separator(value, self.sub_item_separator)):
                self.get_index(index+1).add_value(section, target)
        else:
            self.get_index(self.sub_item_index(target)).add_value(value,target)

    def sub_item_index(self, target: HL7Address):
        return 0


class HL7SubComponent(HL7Item):
    def __init__(self):
        super().__init__()
    def add_value(self, value, target):
        self.value = value

    def sub_item_index(self, target):
        return 1
class HL7Component(HL7Item):
    def __init__(self):
        super().__init__()
        self.sub_items: List[HL7SubComponent] = []
        self.sub_item_separator = '&'
        self.sub_item_type = HL7SubComponent
    
    def sub_item_index(self, target):
        return target.sub_component

class HL7FieldInstance(HL7Item):
    def __init__(self):
        super().__init__()
        self.sub_items:List[HL7Component] = []
        self.sub_item_separator = '^'
        self.sub_item_type = HL7Component
    
    def sub_item_index(self, target):
        return target.component

class HL7Field(HL7Item):
    def __init__(self):
        super().__init__()
        self.sub_items: List[HL7FieldInstance] = []
        self.sub_item_separator = '~'
        self.sub_item_type = HL7FieldInstance

    def sub_item_index(self, target):
        return target.field_index

class HL7SegmentInstance(HL7Item):
    def __init__(self):
        super().__init__()
        self.sub_items:List[HL7Field] = []
        self.sub_item_separator = '|'
        self.sub_item_type=HL7Field
    
    def sub_item_index(self, target):
        return target.field

class HL7Segment(HL7Item):
    def __init__(self, segment: str):
        super().__init__()
        self.segment = segment
        self.indexed = False
        self.sub_items:List[HL7SegmentInstance] = []
        self.sub_item_type =HL7SegmentInstance
        self.sub_item_separator = '\r'

    def __repr__(self):
        return self.__str__()
    def __str__(self):
        if len(self.sub_items) == 0:
            return self.segment + '|1'
        if self.indexed or len(self.sub_items)>1:
            instance_list = []
            for instance in self.sub_items:
                new_instance = HL7SegmentInstance()
                new_instance.sub_items = instance.sub_items[1:]
                instance_list.append(new_instance)
            return "\r".join([f"{self.segment}|{idx+1}|{fields}" for idx, fields in enumerate (instance_list)])
        elif self.segment == 'MSH':
            msh_segdata = HL7SegmentInstance()
            msh_segdata.sub_items=self.sub_items[0].sub_items[2:]
            return f"MSH|^~\\&|{msh_segdata}"
        else:
            return self.segment + '|' + str(self.sub_items[0])
    
    def sub_item_index(self, target):
        return target.index

class HL7Structure(MapSource):
    """
    This class holds an HL7 message as source or destination for mapping purposes
    """
    segments:Dict[str, HL7Segment]
    separators: str
    message: str
    sections: List[str]
    walk_path = ["repetition", "component", "sub_component"]

    def __init__(self, sequence):
        self.segments = {}
        self.separators = '^~\\&'
        self.sequence = sequence
        self.message = ''
        self.sections = []

    def set_message(self, message:str):
        self.message = message
        self.sections = message.strip().split('\r')

    def get_source_segment_headers(self):
        segment_headers = [seg[:3] for seg in self.sections]
        result = []
        for header in segment_headers:
            if header not in result:
                result.append(header)
        return result

    def get_segments(self, header:str):
        return [seg for seg in self.sections if seg.startswith(header+'|')]

    def get_source_segment_repetitions(self, header:str):
        reps = self.get_segments(header[:3])
        return [str(i+1) for i in range(len(reps))]

    def get_header_and_index(self, address:str):
        components = get_cleaned_list(get_cleaned_list(address, '_')[0], '.')
        return components[0], 1 if len(components) == 1 else int(components[1])
    
    def get_source_fields(self, address:str):
        header, index = self.get_header_and_index(address)
        segments = self.get_segments(header)
        if index > len(segments):
            return []
        fields = split_by_separator(segments[index-1],'|')
        if header == 'MSH':
            return [str(i+3) for i in range(len(fields)-2)]
        else:
            return [str(i+1) for i in range(len(fields))]

    def get_hl7_indices(self, address:str, index_of='each-component'):
        content = self.get_component(address[:-2])
        separator = {
            "each-field-repetition": '~',
            'each-component':'^',
            'each-sub-component': '&'
        }[index_of]
        components = split_by_separator(content, separator)
        return [str(i+1) for i in range(len(components))]


    
    def get_component(self, address:str):
        """
        This is the main function that returns the contents of a part of HL7 message
        requested by address.
        The format of the address is SEN.n._F.n_C_S
        were SEG is the segment header, e.g. MSH, PV1, etc.
        SEG.n denotes the nth repetition of that segment in the message. e.g. PV1.1, DG1.2
        F is the index of the field in that segment, eg. PID_3 is the third field of PID (starting form one)
        F.n is the number of repetition of that field in that segment. eg PID_3.2 is the second repetition of the third field  
        C and S are the number for component and sub component in the selected repetition of the field
        if n is ignored in any of the above, it defaults to 1 (selects first repetition of segment or field)
        if n is set to '*' in any of the above, it denotes a collection of all repetitions.
           for example: PV1.*_3 returns an array with the contents of the thrid field of each PV1 segment
           PV1.*.4.* returns an array of each repetition of field 4 in the array of each repetition of PV1 segment.
        """
        source = HL7Address(address)
        segments = self.get_segments(source.segment)
        if len(segments) == 0:
            return ''
        if source.index == '*':
            return [self.get_component(set_segment_index(address, i+1)) for i in range(len(segments))]
        if source.index == 0:
            return self.get_from_segment(segments[0], source)
        elif len(segments) < source.index:
            return ''
        else:
            return self.get_from_segment(segments[source.index-1], source)
        
    def get_separator_for_path(self, step:str)-> str:
        return {
            "field": '|',
            'repetition' : '~',
            'component' : '^',
            'sub_component': '&'
        }[step]
        
    def get_index_for_path(self, source: HL7Address, step: str):
        if step == 'field':
            return source.field
        if step == 'repetition':
            return source.field_index
        if step == 'component':
            return source.component
        if step == 'sub_component':
            return source.sub_component
        
    def walk_down_path(self, target:str, source: HL7Address, path:List[str]):
        separator = self.get_separator_for_path(path[0])
        sections = target.split(separator)
        index = self.get_index_for_path(source, path[0])
        if index == '*':
            return [
                self.walk_down_path(target,
                                    HL7Address(set_field_index(source.address, i+1)), 
                                    path
                                    ) for i in range(len(sections))]
        if index == 0:
            return target
        index = index - 1
        if len(sections) < index + 1:
            return ''
        if path[0] == 'sub_component':
            return sections[index]
        return self.walk_down_path(sections[index], source, path[1:])
        
    def get_from_segment(self, segment:str, source: HL7Address):
        if source.field == 0:
            return segment[4:]
        fields = segment.split('|')
        field_index = source.field if fields[0]!='MSH' else source.field - 1
        if len(fields) < field_index:
            return ''
        return self.walk_down_path(fields[field_index - 1], source, self.walk_path)
    
    def for_each_destination(self, value:str| List[str], destination: HL7Address):
        if isinstance(value, list):
            for index, instance in enumerate(value):
                self.add_destination(instance, set_segment_index(destination.address,index+1))
        else:
            for index in range(len(self.segments[destination.segment].sub_items)):
                self.add_destination(value, set_segment_index(destination.address, index+1))

    def ensure_segment(self, destination: HL7Address):
        if destination.segment not in self.sequence:
            raise Exception(f"segment {destination.segment} not in Sequence column of mapping sheet")
        if destination.segment not in self.segments:
            self.segments[destination.segment] = HL7Segment(destination.segment)


    def add_destination(self, value: str|List[str], target:str):
        destination = HL7Address(target)
        self.ensure_segment(destination)
        if destination.index == '*':
            self.for_each_destination(value, destination)
        else:
            if destination.index > 0:
                self.segments[destination.segment].indexed = True
            self.segments[destination.segment].add_value(value, destination)
        
    def add_to_segment(self, value:str, target:HL7Address):
        """
            puts a value in a segment, at a location specified by address.
            the target shows the segment -> segment_index -> field -> field_repetitin -> component -> sub_component
            after the segment (which would be a header), everything else is a number and is optional
            if that number is zero, it means the value should be inserted at that level.
            the value will then get parsed to components and they get inserted in their correct place. 
        """
        segment = self.segments[target.segment]
        if target.index == 0: # insert the value at segment level:
            for index, segment_instance in enumerate(split_by_separator(value, '\r')):
                segment.get_index(index+1).add_value(segment_instance)
                
                
    def default_segment(self,segment):
        return f"{segment}|"

    def to_er7(self):
        segments = [str(self.segments.get(segment,self.default_segment(segment))) for segment in self.sequence]
        return "\r".join(segments)
    

    def get_source_value(self, key:str):
        if key=='':
            raise Exception("Bad Key for Source")
        return self.get_component(key)
    
    def finalize(self):
        return self.to_er7()
    
    def restart(self):
        self.__init__(self.sequence)

    def initialize(self, row: pd.Series):
        self.set_message(row['message'])

    def has_component(self, key):
        return key != '' and HL7Address.is_valid_address(key)
    
    def to_pandas_df(self, row_collection):
        return pd.DataFrame(row_collection, columns = ['message'])
    
    def put_all(self, destination:MapDestination):
        sections:Dict[str, List[str]] = {}
        sequence = []
        for section in self.sections:
            if section[:3] not in sections:
                sections[section[:3]] = []
                sequence.append(section[:3])
            sections[section[:3]].append(section[4:])
        destination.sequence = sequence + [sq for sq in destination.sequence if sq not in sequence]
        for key in sequence:
            for index, value in enumerate(sections[key]):
                if index == 0:
                    destination.add_destination(value, key)
                else:
                    destination.add_destination('|'.join(value.split('|')[1:]), f"{key}.{index+1}")

class CSVStructure(MapSource):
    output: List[str]
    row: pd.Series

    def __init__(self, sequence: List[str]):
        super().__init__()
        self.sequence = sequence
        self.output = {k.strip():'' for k in self.sequence}

    def get_source_value(self, key:str):
        if key == '':
            raise Exception("Bad key for Source")
        return clean_up(self.row[key])
    
    def finalize(self):
        return [f'{self.output[v]}' for v in self.sequence]
    
    def restart(self):
        self.__init__(self.sequence)
    
    def initialize(self, row):
        self.row = row

    def add_destination(self, value:str, target:str):
        if target not in self.sequence:
            raise Exception(f"Column {target} not specified in mapping Sequence column")
        self.output[target] = value
    
    def has_component(self, key):
        return key != '' and key in self.row
    
    def to_pandas_df(self, row_collection):
        return pd.DataFrame(row_collection, columns=self.sequence)
    
    def put_all(self, destination):
        sequence = [c for c in self.row.index]
        destination.sequence = sequence + [sq for sq in destination.sequence if sq not in sequence]
        destination.restart()
        for key in destination.sequence:
            if key in sequence:
                destination.add_destination(self.row[key], key)
            else:
                destination.add_destination('', key)

