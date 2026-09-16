"""MCP input schemas for the planning workbench's shared operations."""
FILTER_PROPERTIES = {
    'current_chapter': {'type': 'integer', 'minimum': 0, 'maximum': 1000000},
    'status': {'type': 'string', 'enum': ['all', 'open', 'resolved', 'cancelled']},
    'due': {'type': 'string', 'enum': ['all', 'overdue', 'due_soon', 'scheduled', 'unplanned', 'closed']},
    'query': {'type': 'string', 'maxLength': 200}, 'tag': {'type': 'string', 'maxLength': 40},
    'priority': {'type': 'string', 'enum': ['all', 'low', 'normal', 'high']},
    'ownership': {'type': 'string', 'enum': ['all', 'author', 'summary']},
    'due_within': {'type': 'integer', 'minimum': 0, 'maximum': 1000000},
}
_TAGS = {'type': 'array', 'maxItems': 10, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 40}}
BATCH_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['selection', 'changes'], 'properties': {
    'selection': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['id', 'expected_revision'],
        'properties': {'id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                       'expected_revision': {'type': 'integer', 'minimum': 0, 'maximum': 1000000000}}}},
    'changes': {'type': 'object', 'minProperties': 1, 'additionalProperties': False, 'properties': {
        'target_chapter': {'type': 'integer', 'minimum': 1, 'maximum': 1000001},
        'target_delta': {'type': 'integer', 'minimum': -1000000, 'maximum': 1000000},
        'status': {'type': 'string', 'enum': ['open', 'resolved', 'cancelled']},
        'priority': {'type': 'string', 'enum': ['low', 'normal', 'high']},
        'add_tags': _TAGS, 'remove_tags': _TAGS,
        'resolved_chapter': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 1000001},
        'resolution_note': {'type': 'string', 'maxLength': 2000}}},
    'dry_run': {'type': 'boolean', 'default': True, 'description': 'Preview first. Set false only to apply the reviewed batch.'},
}}
REPORT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    **FILTER_PROPERTIES, 'max_items': {'type': 'integer', 'minimum': 1, 'maximum': 5000, 'default': 1000},
    'include_notes': {'type': 'boolean', 'default': False},
}}
