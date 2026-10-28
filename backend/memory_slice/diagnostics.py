"""Safe job diagnostics: never include model output, validation inputs or secrets."""
import traceback
from pathlib import Path
from .errors import MemoryError as HTTPException
from pydantic import ValidationError

MESSAGES = {
    'missing': '缺少必填字段', 'string_type': '应为字符串',
    'list_type': '应为列表', 'dict_type': '应为对象',
    'float_parsing': '无法解析为数字', 'float_type': '应为数字',
    'int_parsing': '无法解析为整数', 'literal_error': '值不在允许的选项中',
    'greater_than_equal': '小于允许的最小值', 'less_than_equal': '超过允许的最大值',
    'string_too_long': '字符串过长', 'string_too_short': '字符串过短',
    'extra_forbidden': '包含不允许的额外字段', 'value_error': '未满足字段约束',
}

def job_diagnostic(exc, stage):
    result = {'stage': stage, 'exception_type': type(exc).__name__, 'code': 'internal_error'}
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        result.update(code='validation_error', error_count=len(errors), issues=[{
            'field': '.'.join(str(p) for p in e['loc'])[:200] if e['type'] != 'extra_forbidden' else '(额外字段)',
            'code': e['type'], 'message': MESSAGES.get(e['type'], '字段类型或约束不符合要求'),
        } for e in errors[:20]])
    elif isinstance(exc, HTTPException):
        result.update(code='http_error', status_code=exc.status_code)
    # Stack locations are useful for unexpected failures; exception text can contain credentials.
    result['frames'] = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                        for f in traceback.extract_tb(exc.__traceback__)[-8:]]
    return result
