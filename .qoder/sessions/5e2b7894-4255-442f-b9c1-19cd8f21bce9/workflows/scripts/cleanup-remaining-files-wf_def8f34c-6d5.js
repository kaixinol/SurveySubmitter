export const meta = {
  name: 'cleanup-remaining-files',
  description: 'Process remaining Python files to fix typing imports, getattr, and .get() calls',
  phases: [
    { title: 'Process AI and Reverse Fill', detail: 'Fix batch_runtime.py and validation.py' },
    { title: 'Process Task files', detail: 'Fix task_context.py, distribution_state.py, progress_state.py, proxy_state.py, reverse_fill_state.py' },
    { title: 'Process Persona and Modes', detail: 'Fix generator.py, context.py, duration_control.py' },
  ],
}

const files = [
  // AI
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/ai/batch_runtime.py',
  // Reverse fill
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/reverse_fill/validation.py',
  // Task
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/task/task_context.py',
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/task/distribution_state.py',
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/task/progress_state.py',
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/task/proxy_state.py',
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/task/reverse_fill_state.py',
  // Persona
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/persona/generator.py',
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/persona/context.py',
  // Modes
  '/mnt/data/Project/SurveyController/src/survey_submitter/core/modes/duration_control.py',
]

phase('Process AI and Reverse Fill')
await parallel([
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V], Tuple[X,...] with tuple[X,...], Set[X] with set[X]
2. Replace Optional[X] with X | None, Union[X,Y] with X | Y
3. Remove unused typing imports (List, Dict, Tuple, Set, Optional, Union, Deque)
4. Add "from __future__ import annotations" if not present
5. For getattr on Pydantic models where the attribute is defined on the model, replace with direct attribute access
6. Keep getattr when the object type is Any or when the attribute might not exist

Specific changes for this file:
- This is batch_runtime.py. It uses SurveyQuestionMeta which has: num, title, type_code, provider_type, required, description, unsupported, unsupported_reason, provider_question_id, provider_page_id
- Replace getattr(question, "num", 0) with question.num (question is SurveyQuestionMeta)
- Replace getattr(question, "title", "") with question.title
- Replace getattr(question, "description", "") with question.description
- Keep getattr(question, "option_texts", []) because option_texts is on ChoiceQuestionMeta (subclass), not SurveyQuestionMeta
- Keep getattr(action, "question_num", 0) because action is AnswerAction (not a model we know)
- Fix all typing imports: List->list, Dict->dict, Tuple->tuple, Optional->X|None

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/ai/batch_runtime.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V], Tuple[X,...] with tuple[X,...], Set[X] with set[X]
2. Replace Optional[X] with X | None, Union[X,Y] with X | Y
3. Remove unused typing imports (List, Dict, Tuple, Set, Optional, Union, Deque)
4. Ensure "from __future__ import annotations" is present
5. For getattr on Pydantic models where the attribute is defined on the model, replace with direct attribute access
6. Keep getattr when the object type is Any or unknown

Specific changes for validation.py:
- It already has from __future__ import annotations
- Replace all List, Dict, Optional with lowercase/pipe syntax
- Remove typing imports for List, Dict, Optional
- Keep getattr on QuestionEntry objects (not a Pydantic model we have definition for)
- Keep getattr on column objects (type Any)
- Keep getattr on config (RuntimeConfig, unknown model)
- Replace getattr(info, "num", None) where info is SurveyQuestionMeta with info.num
- Replace getattr(info, "title", None) where info is SurveyQuestionMeta with info.title

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/reverse_fill/validation.py`),
])

phase('Process Task files')
await parallel([
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V], Tuple[X,...] with tuple[X,...], Set[X] with set[X], Deque[X] with deque[X]
2. Replace Optional[X] with X | None, Union[X,Y] with X | Y
3. Remove unused typing imports (List, Dict, Tuple, Set, Optional, Union, Deque)
4. Ensure "from __future__ import annotations" is present
5. For getattr on Pydantic models where the attribute is defined, replace with direct access

This is task_context.py. Key model: ExecutionConfig(BaseConfigModel) with fields:
url, survey_title, survey_provider, single_prob, droplist_prob, multiple_prob, matrix_prob, scale_prob, slider_targets, texts, texts_prob, text_entry_types, text_ai_flags, text_titles, location_parts, multi_text_blank_modes, multi_text_blank_ai_flags, multi_text_blank_int_ranges, single_option_fill_texts, single_attached_option_selects, droplist_option_fill_texts, multiple_option_fill_texts, answer_rules, reverse_fill_spec, question_config_index_map, provider_question_config_index_map, question_dimension_map, question_ordinal_score_map, question_strict_ratio_map, question_psycho_bias_map, questions_metadata, provider_question_metadata_map, joint_psychometric_answer_plan, psycho_target_alpha, num_threads, target_num, fail_threshold, stop_on_fail_enabled, submit_interval_range_seconds, answer_duration_range_seconds, answer_datetime_window_ms, random_proxy_ip_enabled, proxy_source, proxy_ip_pool, random_user_agent_enabled, user_agent_ratios, pause_on_aliyun_captcha, ai_system_prompt

Replace all typing imports with modern syntax. Deque should use collections.deque.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/task/task_context.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V], Tuple[X,...] with tuple[X,...]
2. Replace Optional[X] with X | None
3. Remove unused typing imports (List, Dict, Tuple, Optional)
4. Ensure "from __future__ import annotations" is present

This is distribution_state.py. Already has from __future__ import annotations.
Just fix typing imports and type annotations.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/task/distribution_state.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V]
2. Remove unused typing imports (List, Dict)
3. Ensure "from __future__ import annotations" is present

This is progress_state.py. Already has from __future__ import annotations.
Just fix typing imports and type annotations.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/task/progress_state.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace Optional[X] with X | None
2. Remove unused typing imports (Optional)
3. Ensure "from __future__ import annotations" is present
4. For getattr on ProxyLease (a dataclass with fields: address, expire_at, expire_ts, poolable, source), replace getattr(lease, "address", "") with lease.address

This is proxy_state.py. Already has from __future__ import annotations.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/task/proxy_state.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace Optional[X] with X | None, Tuple[X,...] with tuple[X,...]
2. Remove unused typing imports (Optional, Tuple)
3. Ensure "from __future__ import annotations" is present
4. Keep getattr on self.config (type Any in Protocol) - don't replace

This is reverse_fill_state.py. Already has from __future__ import annotations.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/task/reverse_fill_state.py`),
])

phase('Process Persona and Modes')
await parallel([
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V]
2. Replace Optional[X] with X | None
3. Remove unused typing imports (List, Dict, Optional)
4. Add "from __future__ import annotations" if not present
5. Keep getattr(_thread_local, "persona", None) - _thread_local is threading.local(), not a Pydantic model

This is persona/generator.py. Needs from __future__ import annotations added.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/persona/generator.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace List[X] with list[X], Dict[K,V] with dict[K,V]
2. Replace Optional[X] with X | None
3. Remove unused typing imports (List, Dict, Optional)
4. Add "from __future__ import annotations" if not present
5. Keep getattr(_thread_local, "answered", None/_thread_local.answered) - _thread_local is threading.local()

This is persona/context.py. Needs from __future__ import annotations added.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/persona/context.py`),
  () => agent(`Read and rewrite this file with cleaned up typing imports.

Rules:
1. Replace Tuple[X,...] with tuple[X,...], Optional[X] with X | None
2. Remove unused typing imports (Tuple, Optional)
3. Ensure "from __future__ import annotations" is present

This is modes/duration_control.py. Already has from __future__ import annotations.

File: /mnt/data/Project/SurveyController/src/survey_submitter/core/modes/duration_control.py`),
])

log('All files processed successfully')
