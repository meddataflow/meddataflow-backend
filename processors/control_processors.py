"""
Workflow Control Activity Processors
Contains control flow processors extracted from WorkflowExecutionService
"""
import asyncio
import re
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

# Import models
from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus

# Import services
from services.hl7_mapper_service import hl7_mapper_service
from processors.hl7_processors import _extract_hl7_field_value

logger = logging.getLogger(__name__)


async def process_custom_code_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process Custom Code activity with safe sandboxed execution
    Supports Python code execution with restricted imports and timeouts
    """
    config = activity.get("config", {})
    code = config.get("code", "") or config.get("script_content", "")
    language = config.get("language", "python") or config.get("script_type", "python")
    input_variables = config.get("input_variables", [])
    output_variables = config.get("output_variables", [])
    allowed_imports = config.get("allowed_imports", ["math", "json", "datetime"])
    timeout_seconds = config.get("timeout_seconds", 30)
    sandbox_mode = config.get("sandbox_mode", True)

    if not code:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No code provided for execution"
        )

    try:
        # Prepare execution context
        execution_context = {
            "context_vars": dict(context.variables),
            "result_vars": {},
            "output_message": ""
        }

        # Add input variables to execution context
        for var_name in input_variables:
            if var_name in context.variables:
                execution_context["context_vars"][var_name] = context.variables[var_name]
            elif var_name in context.variables.get("metadata", {}):
                execution_context["context_vars"][var_name] = context.variables["metadata"][var_name]
            else:
                # Try extracting from HL7 message directly if it looks like an HL7 path
                if "." in var_name and context.raw_message:
                    from services.generic_hl7_mapper import generic_hl7_mapper
                    try:
                        value = generic_hl7_mapper.extract_field_generic(context.raw_message, var_name, "")
                        if value:
                            execution_context["context_vars"][var_name] = value
                            context.variables[var_name] = value  # Store for future use
                    except Exception:
                        pass

        if language.lower() == "python":
            # Use hl7_mapper style execution approach
            # Format code into a function similar to your execute_script method
            # First, handle escaped newlines in the code
            code = code.replace('\\n', '\n')
            code_lines = code.strip().split('\n')
            indented_code = "\n\t".join(code_lines)

            # Create function definition
            script_function = f"""
def custom_script(context_vars, result_vars):
\t{indented_code}
\treturn result_vars
"""
            # Import secure sandbox for safe code execution
            try:
                from security.secure_sandbox import sandbox, SecurityError

                # Get the raw script content from config
                raw_script = config.get("script", "")

                if not raw_script:
                    # Fallback: extract from function if needed
                    script_lines = script_function.split('\n')
                    function_body_lines = []
                    in_function = False

                    for line in script_lines:
                        if line.strip().startswith('def custom_script'):
                            in_function = True
                            continue
                        elif in_function and line.strip() and not line.startswith('\t'):
                            break  # End of function
                        elif in_function:
                            # Remove the tab indentation
                            function_body_lines.append(line[1:] if line.startswith('\t') else line)

                    raw_script = '\n'.join(function_body_lines)

                # Execute raw user script in secure sandbox
                sandbox_result = sandbox.execute_safe(
                    raw_script,
                    execution_context["context_vars"],
                    allowed_modules=allowed_imports,
                    timeout_seconds=5
                )

            except SecurityError as e:
                logger.error(f"Security violation in custom script: {e}")
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    output_data={
                        "message": f"Security error: {str(e)}",
                        "error": str(e),
                        "error_type": "SECURITY_VIOLATION",
                        "execution_time_ms": 0
                    },
                    variables=context.variables
                )

            except Exception as e:
                logger.error(f"Error executing custom script: {e}")
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    output_data={
                        "message": f"Execution error: {str(e)}",
                        "error": str(e),
                        "error_type": "EXECUTION_ERROR",
                        "execution_time_ms": 0
                    },
                    variables=context.variables
                )

            # Unpack sandbox execution result
            local_vars: Dict[str, Any] = {}
            returned_result_vars: Dict[str, Any] = {}
            output_message = ""

            if isinstance(sandbox_result, dict):
                # Preferred structure returned by sandbox.execute_safe
                returned_result_vars = sandbox_result.get("result_vars", {}) or {}
                local_vars = sandbox_result.get("local_vars", {}) or {}
                output_message = sandbox_result.get("output_message", "") or ""
            else:
                # Backward compatibility if sandbox returns just the dict of result vars
                returned_result_vars = sandbox_result or {}

            # Merge returned result vars into execution context
            if returned_result_vars:
                execution_context["result_vars"].update(returned_result_vars)

            # Extract output_message if provided
            execution_context["output_message"] = output_message

            # Update context variables with output variables
            # First check the function's local variables, then result_vars
            for var_name in output_variables:
                if var_name in local_vars:
                    context.variables[var_name] = local_vars[var_name]
                elif var_name in execution_context["result_vars"]:
                    context.variables[var_name] = execution_context["result_vars"][var_name]

            # Create comprehensive output
            # Sanitize local_vars to include only JSON-safe primitives
            sanitized_local_vars: Dict[str, Any] = {}
            for k, v in (local_vars or {}).items():
                if k.startswith("__"):
                    continue
                if isinstance(v, (str, int, float, bool)) or v is None:
                    sanitized_local_vars[k] = v

            code_output = {
                "language": language,
                "code_executed": True,
                "execution_successful": True,
                "output_message": execution_context["output_message"],
                "variables_updated": {var: context.variables.get(var) for var in output_variables if var in context.variables},
                "local_variables": sanitized_local_vars,
                "timeout_seconds": timeout_seconds
            }

            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": "Custom code executed successfully",
                    "code_output": code_output,
                    "script_output": code_output,  # Alias for test compatibility
                    "execution_result": execution_context["output_message"] or "Code executed without output",
                    "variables_created": list(execution_context["result_vars"].keys()),
                    "execution_time_ms": 0  # Placeholder since we don't measure exact time
                },
                variables=context.variables
            )

        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=f"Unsupported language: {language}. Only Python is currently supported."
            )

    except Exception as e:
        logger.error(f"Error executing custom code: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            output_data={
                "code_executed": False,
                "execution_successful": False,
                "error_details": str(e),
                "language": language
            },
            error_message=f"Custom code execution failed: {str(e)}"
        )


async def process_validation_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """Process Validation activity"""
    config = activity.get("config", {})
    # Accept both legacy 'rules' and frontend 'validation_rules'
    validation_rules = config.get("rules", [])
    fe_rules = config.get("validation_rules", [])
    if fe_rules and not validation_rules:
        # Normalize frontend rules to legacy shape where possible
        normalized = []
        for r in fe_rules:
            vt = (r.get("validation_type") or "").lower()
            fld = r.get("field_path") or r.get("field")
            expected = r.get("expected_value")
            rule: Dict[str, Any] = {}
            # Keep original fields for HL7 extraction in evaluation
            rule["field_path"] = fld
            rule["validation_type"] = vt
            rule["expected_value"] = expected
            # Provide best-effort mapping to legacy keys for simple required/regex
            if vt == "required":
                rule["required"] = True
            elif vt == "regex" and expected:
                rule["pattern"] = expected
            normalized.append(rule)
        validation_rules = normalized

    validation_errors = []

    for rule in validation_rules:
        # Support both variable-based and HL7 field_path validation
        field = rule.get("field")
        field_path = rule.get("field_path")
        required = rule.get("required", False)
        min_length = rule.get("min_length")
        max_length = rule.get("max_length")
        pattern = rule.get("pattern")
        vt = (rule.get("validation_type") or "").lower()
        expected_value = rule.get("expected_value")

        # Determine value: prefer HL7 extraction if field_path contains a segment
        value = None
        if field_path:
            try:
                value = _extract_hl7_field_value(context.raw_message or "", field_path, "")
            except Exception:
                value = None
        if value is None and field:
            value = context.variables.get(field)

        if required and not value:
            validation_errors.append(f"{field} is required")

        # Frontend-style validations
        if vt == "required" and (value is None or str(value) == ""):
            validation_errors.append(f"{field_path or field or 'value'} is required")

        if value and min_length and len(str(value)) < min_length:
            validation_errors.append(f"{field} must be at least {min_length} characters")

        if value and max_length and len(str(value)) > max_length:
            validation_errors.append(f"{field} must be at most {max_length} characters")

        if value and pattern:
            if not re.match(pattern, str(value)):
                validation_errors.append(f"{field} does not match required pattern")
        # Frontend regex
        if value and vt == "regex" and expected_value:
            try:
                if not re.match(str(expected_value), str(value)):
                    validation_errors.append(f"{field_path or field or 'value'} does not match pattern")
            except Exception:
                pass

    if validation_errors:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="; ".join(validation_errors),
            output_data={"validation_errors": validation_errors},
            variables={"validation_passed": False}  # Pass validation failure to next activities
        )

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"validation_passed": True, "rules_checked": len(validation_rules)},
        variables={"validation_passed": True}  # Pass validation result to next activities
    )


async def process_content_router_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Content-Based Router
    Evaluates a list of route rules against variables and sets routing variables.

    Config schema (example):
    {
      "routes": [
        {"match": {"variable": "MESSAGE_TYPE", "operator": "equals", "value": "ADT^A04"}, "action": "route", "route": "ADT"},
        {"match": {"variable": "MESSAGE_TYPE", "operator": "starts_with", "value": "ORM"}, "action": "skip", "skip_next_n": 2},
        {"match": {"variable": "ERROR_FLAG", "operator": "equals", "value": "1"}, "action": "stop"}
      ],
      "default_route": "default"
    }
    """
    cfg = activity.get("config", {}) or {}
    routes = cfg.get("routes", []) or []
    default_route = cfg.get("default_route")

    # Reuse core condition eval
    try:
        from .core_processors import _evaluate_condition as _eval
    except Exception:
        def _eval(actual_value, operator, expected_value):
            a = str(actual_value or "")
            e = str(expected_value or "")
            if operator == "equals":
                return a == e
            if operator == "not_equals":
                return a != e
            if operator == "contains":
                return e in a
            if operator == "starts_with":
                return a.startswith(e)
            if operator == "ends_with":
                return a.endswith(e)
            return False

    decided = {
        "route": None,
        "skip_next_n": 0,
        "stop_workflow": False
    }

    for rule in routes:
        match = (rule or {}).get("match", {})
        var = match.get("variable")
        operator = match.get("operator", "equals")
        expected = match.get("value")
        actual = context.variables.get(var)
        if _eval(actual, operator, expected):
            action = (rule.get("action") or "route").lower()
            if action == "route":
                decided["route"] = rule.get("route")
            elif action == "skip":
                try:
                    decided["skip_next_n"] = int(rule.get("skip_next_n") or 0)
                except Exception:
                    decided["skip_next_n"] = 0
            elif action == "stop":
                decided["stop_workflow"] = True
            break

    if not decided["route"] and default_route:
        decided["route"] = default_route

    out = {
        "message": "Routing evaluated",
        "route": decided["route"],
        "skip_next_n": decided["skip_next_n"],
        "stop_workflow": decided["stop_workflow"]
    }

    # Inject variables for downstream activities
    vars_update = {k: v for k, v in decided.items() if v}

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data=out,
        variables=vars_update
    )


async def process_condition_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """Process Condition activity with support for multiple conditions, field extraction, and frontend on_true/on_false actions"""
    config = activity.get("config", {})
    conditions = config.get("conditions", [])
    default_action = config.get("default_action", {})
    extract_config = config.get("extract_from_message", {})

    # Frontend-style configuration support
    on_true_action = config.get("on_true", "continue")
    on_false_action = config.get("on_false", "continue")
    condition_variable = config.get("condition_variable")
    condition_operator = config.get("condition_operator", "equals")
    condition_value = config.get("condition_value")

    # Extract fields from HL7 message if specified
    if extract_config and context.raw_message:
        segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)

        for var_name, field_config in extract_config.items():
            segment_name = field_config.get("segment")
            field_number = field_config.get("field")

            if segment_name in segments and segments[segment_name]:
                segment = segments[segment_name][0]  # Use first occurrence
                value = hl7_mapper_service.extract_segment_field(segment, field_number)
                context.variables[var_name] = value

    # Evaluate conditions (both old and new format)
    condition_met = False
    action_taken = None
    matching_condition = None

    # New frontend-style single condition evaluation
    if condition_variable and condition_value is not None:
        if condition_variable in context.variables:
            actual_value = context.variables[condition_variable]
            condition_met = _evaluate_condition(actual_value, condition_operator, condition_value)
        else:
            logger.warning(f"🔧 CONDITION_EVAL: Variable '{condition_variable}' NOT FOUND in context variables: {list(context.variables.keys())}")

        # Execute appropriate action based on result
        if condition_met:
            action_taken = _execute_frontend_condition_action(on_true_action, context)
            context.variables["condition_result"] = "true"
            context.variables["selected_action"] = on_true_action
        else:
            action_taken = _execute_frontend_condition_action(on_false_action, context)
            context.variables["condition_result"] = "false"
            context.variables["selected_action"] = on_false_action

    # Legacy multi-condition support with frontend action integration
    if not condition_variable and conditions:
        for condition in conditions:
            variable = condition.get("variable")
            operator = condition.get("operator")
            value = condition.get("value")
            action = condition.get("action")
            action_config = condition.get("action_config", {})


            if variable in context.variables:
                actual_value = context.variables[variable]

                # Evaluate condition based on operator
                if _evaluate_condition(actual_value, operator, value):
                    condition_met = True
                    matching_condition = condition
                    # Use frontend-style action instead of legacy action
                    action_taken = _execute_frontend_condition_action(on_true_action, context)
                    break
            else:
                logger.warning(f"🔧 CONDITION_EVAL: Variable '{variable}' NOT FOUND in context")

        # Execute default action if no conditions matched (use on_false_action)
        if not condition_met:
            action_taken = _execute_frontend_condition_action(on_false_action, context)

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "message": "Condition evaluation completed",
            "condition_met": condition_met,
            "matching_condition": matching_condition,
            "action_taken": action_taken,
            "extracted_variables": {k: v for k, v in context.variables.items() if k in extract_config} if extract_config else {},
            "gender_category": context.variables.get("gender_category"),  # Include result variable
            "selected_path": context.variables.get("selected_path", ""),
            "condition_result": context.variables.get("condition_result", ""),
            "selected_action": context.variables.get("selected_action", "")
        },
        variables=context.variables
    )


async def process_delay_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """Process delay activity"""
    config = activity.get("config", {})
    delay_seconds = config.get("delay_seconds", 1)
    max_delay = config.get("max_delay_seconds", 300)  # 5 minutes max
    # Accept frontend alias 'delay_message' for reason
    reason = config.get("reason") or config.get("delay_message", "Processing delay")

    try:
        # Ensure delay is within reasonable bounds
        actual_delay = min(max(delay_seconds, 0.1), max_delay)

        # Sleep for the specified duration
        await asyncio.sleep(actual_delay)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "delay_seconds": actual_delay,
                "delay_reason": reason,
                "max_delay_allowed": max_delay
            }
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            output_data={},
            error_message=f"Delay execution failed: {str(e)}"
        )


async def process_loop_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process sophisticated loop activity with HL7-aware iteration modes
    Supports multiple iteration patterns similar to hl7_mapper ForLoopEvaluator
    """
    config = activity.get("config", {})
    loop_mode = config.get("mode", "each")  # each, each-hl7-item, repeat
    max_iterations = config.get("max_iterations", 100)
    variable_name = config.get("variable_name", "loop_item")
    index_variable = config.get("index_variable", "loop_index")

    try:
        iterations = 0
        loop_results = []
        loop_data = []

        if loop_mode == "each":
            # Simple iteration over array/list
            source = config.get("source", "")
            if source.startswith("variable:"):
                # Get from context variables
                var_name = source.replace("variable:", "")
                items = context.variables.get(var_name, [])
            elif ";;" in source:
                # Split delimited values
                items = [item.strip() for item in source.split(";;")]
            else:
                # Single item
                items = [source] if source else []

            if not isinstance(items, list):
                items = [items]

            for i, item in enumerate(items[:max_iterations]):
                iterations += 1
                context.variables[variable_name] = item
                context.variables[index_variable] = i + 1

                loop_results.append({
                    "iteration": i + 1,
                    "item": item,
                    "variable_name": variable_name,
                    "status": "processed"
                })
                loop_data.append(item)

        elif loop_mode == "each-hl7-item" and context.raw_message:
            # HL7-specific iteration modes
            hl7_target = config.get("hl7_target", "")
            segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)

            if "." in hl7_target:
                # Field-level iteration (e.g., "OBX.5" for all OBX observation values)
                segment_name, field_num = hl7_target.split(".", 1)
                field_number = int(field_num) if field_num.isdigit() else 1

                if segment_name in segments:
                    for i, segment in enumerate(segments[segment_name][:max_iterations]):
                        iterations += 1
                        field_value = hl7_mapper_service.extract_segment_field(segment, field_number)

                        context.variables[variable_name] = field_value
                        context.variables[index_variable] = i + 1
                        context.variables[f"{variable_name}_segment"] = segment

                        loop_results.append({
                            "iteration": i + 1,
                            "segment_type": segment_name,
                            "field_number": field_number,
                            "field_value": field_value,
                            "status": "processed"
                        })
                        loop_data.append(field_value)
            else:
                # Segment-level iteration (e.g., "OBX" for all OBX segments)
                segment_name = hl7_target

                if segment_name in segments:
                    for i, segment in enumerate(segments[segment_name][:max_iterations]):
                        iterations += 1

                        context.variables[variable_name] = segment
                        context.variables[index_variable] = i + 1

                        loop_results.append({
                            "iteration": i + 1,
                            "segment_type": segment_name,
                            "segment_data": segment,
                            "status": "processed"
                        })
                        loop_data.append(segment)

        elif loop_mode == "repeat":
            # Simple repeat loop
            repeat_count = config.get("repeat_count", 1)
            for i in range(min(repeat_count, max_iterations)):
                iterations += 1
                context.variables[index_variable] = i + 1

                loop_results.append({
                    "iteration": i + 1,
                    "status": "processed"
                })

        # Execute actions within loop if specified
        actions = config.get("actions", [])
        final_action_results = {}

        for action in actions:
            action_type = action.get("type")
            if action_type == "set_variable":
                var_name = action.get("variable")
                var_value = action.get("value", "")

                # Support variable substitution
                for key, value in context.variables.items():
                    var_value = str(var_value).replace(f"{{{{{key}}}}}", str(value))

                # For loop actions, collect results from all iterations
                if var_name not in final_action_results:
                    final_action_results[var_name] = []
                final_action_results[var_name].append(var_value)

                # Also set the final value in context
                context.variables[var_name] = var_value

        # Set aggregated results
        for var_name, values in final_action_results.items():
            context.variables[f"{var_name}_all"] = values
            context.variables[f"{var_name}_count"] = len(values)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Loop execution completed",
                "loop_mode": loop_mode,
                "iterations_completed": iterations,
                "loop_results": loop_results,
                "loop_data": loop_data,
                "variable_name": variable_name,
                "index_variable": index_variable,
                "variables_updated": {k: v for k, v in context.variables.items() if k in [variable_name, index_variable]}
            },
            variables=context.variables
        )

    except Exception as e:
        logger.error(f"Error in loop activity: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            output_data={
                "loop_mode": loop_mode,
                "iterations_completed": iterations,
                "error_details": str(e)
            },
            error_message=f"Loop execution failed: {str(e)}"
        )


# Helper functions for condition processing

def _evaluate_condition(actual_value: Any, operator: str, expected_value: Any) -> bool:
    """Evaluate a condition"""
    actual_str = str(actual_value) if actual_value is not None else ""
    expected_str = str(expected_value) if expected_value is not None else ""

    if operator == "equals":
        return actual_str == expected_str
    elif operator == "not_equals":
        return actual_str != expected_str
    elif operator == "contains":
        return expected_str in actual_str
    elif operator == "starts_with":
        return actual_str.startswith(expected_str)
    elif operator == "ends_with":
        return actual_str.endswith(expected_str)
    elif operator == "greater_than":
        try:
            return float(actual_str) > float(expected_str)
        except:
            return False
    elif operator == "less_than":
        try:
            return float(actual_str) < float(expected_str)
        except:
            return False
    elif operator == "is_empty":
        return not actual_str
    elif operator == "is_not_empty":
        return bool(actual_str)
    else:
        return False


def _execute_condition_action(action: str, action_config: Dict[str, Any], context: WorkflowContext) -> str:
    """Execute the action specified by a condition"""
    if action == "set_variable":
        variable = action_config.get("variable")
        value = action_config.get("value")
        if variable and value is not None:
            context.variables[variable] = value
            return f"Set {variable} = {value}"
    elif action == "set_path":
        path = action_config.get("path")
        if path:
            context.variables["selected_path"] = path
            return f"Selected path: {path}"
    return f"Executed action: {action}"


def _execute_frontend_condition_action(action: str, context: WorkflowContext) -> str:
    """Execute frontend-style condition actions (on_true/on_false)"""
    if action == "continue":
        return "Continue to next activity"
    elif action == "skip":
        context.variables["skip_next_activity"] = True
        return "Skip next activity"
    elif action == "stop":
        context.variables["stop_workflow"] = True
        return "Stop workflow"
    return f"Executed frontend action: {action}"
