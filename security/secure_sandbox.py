"""
Secure Sandbox for User Code Execution in HL7 Workflows
Provides restricted execution environment for data transformation scripts
"""
import ast
import math
import re
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
import logging

logger = logging.getLogger(__name__)

class SecurityError(Exception):
    """Raised when code violates security policies"""
    pass

class RestrictedNodeVisitor(ast.NodeVisitor):
    """AST visitor that checks for dangerous constructs"""

    ALLOWED_NODES = {
        ast.Module, ast.FunctionDef, ast.Return, ast.Assign, ast.AnnAssign,
        ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
        ast.Expr, ast.Call, ast.Name, ast.Load, ast.Store, ast.Subscript,
        ast.Index, ast.Slice, ast.List, ast.Tuple, ast.Dict, ast.Set,
        ast.Constant, ast.Str, ast.Num, ast.NameConstant,  # Python < 3.8 compat
        ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp,
        ast.And, ast.Or, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
        ast.In, ast.NotIn, ast.Not, ast.UAdd, ast.USub,
        ast.ListComp, ast.SetComp, ast.DictComp, ast.comprehension,
        ast.keyword, ast.arg, ast.arguments, ast.Attribute,
        ast.IfExp, ast.JoinedStr, ast.FormattedValue,
        ast.Try, ast.ExceptHandler, ast.Raise
    }

    FORBIDDEN_NAMES = {
        '__import__', 'eval', 'exec', 'compile', 'open', 'file', 'input', 'raw_input',
        'globals', 'locals', 'vars', 'dir', 'hasattr', 'getattr', 'setattr', 'delattr',
        'isinstance', 'issubclass', 'callable', '__builtins__', '__file__', '__name__',
        'exit', 'quit', 'help', 'license', 'copyright', 'credits',
        'reload', 'breakpoint', 'memoryview', 'property', 'staticmethod', 'classmethod',
        'super', 'type', 'object', 'id', 'hash', 'repr', 'ascii', 'oct', 'hex', 'bin'
    }

    FORBIDDEN_ATTRIBUTES = {
        '__class__', '__bases__', '__subclasses__', '__mro__', '__dict__',
        '__getattribute__', '__setattr__', '__delattr__', '__getattr__',
        '__module__', '__qualname__', '__annotations__', '__closure__',
        '__code__', '__defaults__', '__globals__', '__kwdefaults__'
    }

    def visit(self, node):
        if type(node) not in self.ALLOWED_NODES:
            raise SecurityError(f"Forbidden AST node type: {type(node).__name__}")
        return super().visit(node)

    def visit_Name(self, node):
        if node.id in self.FORBIDDEN_NAMES:
            raise SecurityError(f"Access to forbidden name: {node.id}")
        return self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in self.FORBIDDEN_ATTRIBUTES:
            raise SecurityError(f"Access to forbidden attribute: {node.attr}")
        return self.generic_visit(node)

    def visit_Import(self, node):
        raise SecurityError("Import statements are forbidden")

    def visit_ImportFrom(self, node):
        raise SecurityError("Import statements are forbidden")

class SecureSandbox:
    """Secure execution environment for user scripts"""

    def __init__(self):
        self.safe_builtins = {
            # Type constructors
            'int': int, 'float': float, 'str': str, 'bool': bool,
            'list': list, 'tuple': tuple, 'dict': dict, 'set': set,
            # Utility functions
            'len': len, 'sum': sum, 'min': min, 'max': max, 'abs': abs,
            'round': round, 'sorted': sorted, 'reversed': reversed,
            'enumerate': enumerate, 'zip': zip, 'filter': filter,
            'map': map, 'any': any, 'all': all,
            # Math operations
            'divmod': divmod, 'pow': pow,
            # Safe printing for debugging
            'print': print,
            # Exception types for error handling
            'Exception': Exception, 'ValueError': ValueError, 'TypeError': TypeError,
            'KeyError': KeyError, 'IndexError': IndexError, 'AttributeError': AttributeError,
            'ZeroDivisionError': ZeroDivisionError, 'RuntimeError': RuntimeError
        }

        self.safe_modules = {
            'math': self._get_safe_math(),
            'json': self._get_safe_json(),
            'datetime': self._get_safe_datetime(),
            're': self._get_safe_regex()
        }

    def _get_safe_math(self):
        """Provide safe math functions"""
        return {
            'ceil': math.ceil, 'floor': math.floor, 'trunc': math.trunc,
            'sqrt': math.sqrt, 'pow': math.pow, 'log': math.log,
            'log10': math.log10, 'log2': math.log2,
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'asin': math.asin, 'acos': math.acos, 'atan': math.atan, 'atan2': math.atan2,
            'degrees': math.degrees, 'radians': math.radians,
            'pi': math.pi, 'e': math.e, 'inf': math.inf, 'nan': math.nan,
            'isnan': math.isnan, 'isinf': math.isinf, 'isfinite': math.isfinite,
            'fabs': math.fabs, 'factorial': math.factorial
        }

    def _get_safe_json(self):
        """Provide safe JSON functions"""
        return {
            'loads': json.loads,
            'dumps': json.dumps
        }

    def _get_safe_datetime(self):
        """Provide safe datetime functions"""
        return {
            'datetime': datetime,
            'timedelta': timedelta,
            'now': datetime.now,
            'strptime': datetime.strptime,
            'strftime': lambda dt, fmt: dt.strftime(fmt)
        }

    def _get_safe_regex(self):
        """Provide safe regex functions"""
        return {
            'search': re.search,
            'match': re.match,
            'findall': re.findall,
            'sub': re.sub,
            'split': re.split,
            'escape': re.escape
        }

    def create_safe_globals(self, allowed_modules: List[str] = None) -> Dict[str, Any]:
        """Create safe global namespace"""
        if allowed_modules is None:
            allowed_modules = ['math', 'json', 'datetime', 're']

        safe_globals = {
            '__builtins__': self.safe_builtins.copy()
        }

        # Add SecurityError to builtins for error handling
        safe_globals['__builtins__']['SecurityError'] = SecurityError

        # Add safe modules
        for module_name in allowed_modules:
            if module_name in self.safe_modules:
                safe_globals[module_name] = self.safe_modules[module_name]

        return safe_globals

    def validate_code(self, code: str) -> None:
        """Validate code using AST analysis"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise SecurityError(f"Syntax error in code: {e}")

        visitor = RestrictedNodeVisitor()
        visitor.visit(tree)

        # Additional security checks
        if len(code) > 10000:  # 10KB limit
            raise SecurityError("Code too long (max 10KB)")

        # Check for suspicious patterns
        suspicious_patterns = [
            r'__.*__',  # Dunder methods
            r'eval\s*\(',
            r'exec\s*\(',
            r'compile\s*\(',
            r'open\s*\(',
            r'import\s+',
            r'from\s+.*\s+import',
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                raise SecurityError(f"Suspicious pattern detected: {pattern}")

    def execute_safe(self, code: str, context_vars: Dict[str, Any],
                    allowed_modules: List[str] = None,
                    timeout_seconds: int = 5) -> Dict[str, Any]:
        """Execute code in secure sandbox with timeout"""

        # Validate code first
        self.validate_code(code)

        # Create safe execution environment
        safe_globals = self.create_safe_globals(allowed_modules)

        # Initialize result_vars that user can modify
        result_vars = {}

        # Set up local variables for user script
        local_vars = {
            'context_vars': context_vars.copy(),
            'result_vars': result_vars
        }

        # Add HL7 specific helper functions
        local_vars.update(self._get_hl7_helpers())

        # Add helper functions to safe_globals
        safe_globals.update(self._get_hl7_helpers())

        try:
            # Wrap user code in a function to support return statements
            # Simple approach: if user returns None/nothing, return result_vars
            indented_code = '\n'.join(['    ' + line for line in code.split('\n')])
            wrapped_code = f"""
def user_script(context_vars, result_vars):
{indented_code}
    # Always return result_vars if nothing else was returned
    return result_vars

# Execute the user script with the variables
_user_result = user_script(context_vars, result_vars)
# If user function returned None (from bare return), use result_vars
_result = _user_result if _user_result is not None else result_vars
"""

            # Add variables to safe_globals for the script execution
            safe_globals['context_vars'] = context_vars.copy()
            safe_globals['result_vars'] = result_vars

            # Execute with timeout protection (cross-platform)
            import threading
            import time

            result_container = {'result': None, 'error': None}

            def execute_with_timeout():
                try:
                    exec(wrapped_code, safe_globals)
                    result = safe_globals.get('_result', {})

                    # Validate result
                    if not isinstance(result, dict):
                        result_container['error'] = SecurityError("Script must return a dictionary")
                    else:
                        # Return both result_vars and local context for compatibility
                        result_container['result'] = {
                            'result_vars': result,
                            'local_vars': safe_globals.copy(),  # Include execution context
                            'output_message': safe_globals.get('output_message', '')
                        }
                except Exception as e:
                    result_container['error'] = e

            # Execute in thread with timeout
            thread = threading.Thread(target=execute_with_timeout)
            thread.daemon = True
            thread.start()
            thread.join(timeout_seconds)

            if thread.is_alive():
                raise SecurityError(f"Script execution timed out after {timeout_seconds} seconds")

            if result_container['error']:
                raise result_container['error']

            return result_container['result']

        except Exception as e:
            logger.error(f"Sandbox execution error: {e}")
            raise SecurityError(f"Script execution failed: {str(e)}")

    def _get_hl7_helpers(self) -> Dict[str, Any]:
        """Provide HL7-specific helper functions"""
        def hl7_get_field(msg_dict, segment, field, component=None):
            """Safe helper to get HL7 field values"""
            try:
                if segment in msg_dict:
                    seg_data = msg_dict[segment]
                    if isinstance(seg_data, list) and len(seg_data) > field:
                        value = seg_data[field]
                        if component is not None and isinstance(value, str) and '^' in value:
                            components = value.split('^')
                            return components[component] if component < len(components) else ""
                        return value
                return ""
            except Exception:
                return ""

        def hl7_set_field(result_dict, key, value):
            """Safe helper to set result values"""
            if isinstance(key, str) and len(key) < 100:  # Reasonable key length
                result_dict[key] = str(value)[:1000]  # Limit value length

        def hl7_format_date(date_str, input_fmt="%Y%m%d", output_fmt="%Y-%m-%d"):
            """Safe date formatting for HL7"""
            try:
                dt = datetime.strptime(date_str, input_fmt)
                return dt.strftime(output_fmt)
            except Exception:
                return date_str

        def hl7_clean_text(text):
            """Clean and sanitize text fields"""
            if not isinstance(text, str):
                text = str(text)
            # Remove control characters but keep basic punctuation
            return re.sub(r'[^\w\s\.\,\-\(\)\/]', '', text)[:500]  # Limit length

        return {
            'hl7_get_field': hl7_get_field,
            'hl7_set_field': hl7_set_field,
            'hl7_format_date': hl7_format_date,
            'hl7_clean_text': hl7_clean_text
        }

# Global sandbox instance
sandbox = SecureSandbox()