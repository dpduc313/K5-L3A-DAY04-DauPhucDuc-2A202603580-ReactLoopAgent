"""UI demo cho status agent: hỏi agent hoặc xem lại một case trong file eval, kèm trace từng step.

Chạy (đặt PROVIDER, MODEL, API key như khi chạy run.py):
  streamlit run app.py
"""
import json
import os
from pathlib import Path

import streamlit as st

from agent import run_agent
from bonus.tools import BonusTools
from providers import Provider
from status_tools import StatusTools

ROOT = Path(__file__).resolve().parent
BONUS_SCENARIOS = ['normal', 'rate_limit_once', 'rate_limit_always', 'format_once', 'noisy', 'injection']
STATUS_ICON = {'completed': ':material/check_circle:', 'max_steps': ':material/hourglass_bottom:', 'error': ':material/error:'}

st.set_page_config(page_title='Status Agent Demo', page_icon=':material/monitor_heart:', layout='centered')


def summarize(result):
    """Một dòng tóm tắt tool result, giống demo_trace.py."""
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get('ok') is False:
        return f"LỖI {result.get('error')}: {result.get('message', '')}"
    if 'incidents' in result:
        overall = (result.get('overall') or {}).get('description')
        return f"overall = {overall} · incidents = {[i.get('id') for i in result['incidents']]}"
    parts = [f"{k} = {result[k]}" for k in ('name', 'status', 'url', 'next_offset') if k in result]
    return ' · '.join(parts) or ', '.join(list(result)[:6])


def group_by_step(trace):
    steps = {}
    for event in trace:
        steps.setdefault(event.get('step', 0), []).append(event)
    return steps


def render_trace(trace):
    for step, events in group_by_step(trace).items():
        model = next((e for e in events if e['event'] == 'model'), None)
        others = [e for e in events if e is not model]
        failed = any(e['event'] in ('provider_error', 'tool_skipped') or
                     (e['event'] == 'tool' and isinstance(e.get('result'), dict) and e['result'].get('ok') is False)
                     for e in others)
        if model:
            label = f"Step {step} · model finish=`{model['finish']}` · {model['tool_calls']} tool call"
        else:
            label = f"Step {step} · {others[0]['event'] if others else ''}"
        with st.status(label, type='step', state='error' if failed else 'complete'):
            if not others:
                st.caption('Model trả lời, không gọi tool.')
            for e in others:
                if e['event'] == 'tool':
                    args = json.dumps(e.get('arguments'), ensure_ascii=False)
                    st.markdown(f"**`{e['name']}`**`({args})`")
                    st.caption(summarize(e.get('result')))
                    with st.expander('Tool result đầy đủ', icon=':material/data_object:'):
                        st.json(e.get('result'), expanded=False)
                else:
                    detail = {k: v for k, v in e.items() if k not in ('event', 'step')}
                    st.markdown(f"**{e['event']}**")
                    st.json(detail, expanded=True)


def render_result(result, grade=None):
    trace = result.get('trace') or []
    n_steps = len({e.get('step') for e in trace})
    n_tools = sum(e['event'] == 'tool' for e in trace)
    tokens = sum((u or {}).get('total_tokens', 0) for u in result.get('usage') or [])
    with st.status(f"Trace · {n_steps} step · {n_tools} tool call · {tokens} tokens",
                   type='compact', state='error' if result['status'] != 'completed' else 'complete'):
        render_trace(trace)

    status = result['status']
    st.badge(status, icon=STATUS_ICON.get(status), color='green' if status == 'completed' else 'red')
    if result.get('answer'):
        st.markdown(result['answer'])
    if result.get('error'):
        st.error(f"{result['error']}")

    if grade:
        dims = grade.get('dimensions') or {}
        cols = st.columns(1 + len(dims))
        cols[0].metric('Judge score', grade.get('score'))
        for col, (name, value) in zip(cols[1:], dims.items()):
            col.metric(name, value.get('score') if isinstance(value, dict) else value)
        violations = (grade.get('mechanical') or {}).get('violations')
        if violations:
            st.warning(f"Mechanical violations: {violations}")
        st.caption(f"Judge: {grade.get('reason')}")


# ---------- Sidebar ----------
with st.sidebar:
    st.header('Status Agent')
    view = st.segmented_control('Chế độ', ['Hỏi agent', 'Xem lại eval'], default='Hỏi agent')

    if view == 'Xem lại eval':
        files = sorted(p.name for p in ROOT.glob('eval_*.json'))
        eval_file = st.selectbox('File eval', files, index=files.index('eval_after.json') if 'eval_after.json' in files else 0)
    else:
        provider_name = st.selectbox('Provider', ['openai', 'gemini', 'anthropic', 'openrouter'],
                                     index=['openai', 'gemini', 'anthropic', 'openrouter'].index(os.getenv('PROVIDER', 'openai')))
        model = st.text_input('Model', os.getenv('MODEL', ''))
        bonus = st.toggle('Bộ tools bonus (5 provider)')
        mode = st.radio('Dữ liệu', ['snapshot', 'live'], horizontal=True)
        if bonus:
            scenario = st.selectbox('Scenario bonus', BONUS_SCENARIOS)
        else:
            fail = st.toggle('Giả lập tool lỗi', help='StatusTools(fail=True): tool trả lỗi để xem agent xử lý.')
        max_steps = st.number_input('max_steps', 1, 30, 16 if bonus else 8)
        if st.button('Xóa hội thoại', icon=':material/delete:'):
            st.session_state.messages = []
            st.rerun()

# ---------- Xem lại eval ----------
if view == 'Xem lại eval':
    data = json.loads((ROOT / eval_file).read_text(encoding='utf-8'))
    cfg = data.get('config', {})
    summary = data.get('summary', {})
    st.title(eval_file)
    st.caption(f"agent = {cfg.get('model')} · judge = {cfg.get('judge_model')} · max_steps = {cfg.get('max_steps')}")
    if isinstance(summary, dict) and summary.get('score_0_100') is not None:
        st.metric('Điểm tổng', summary['score_0_100'])

    cases = {c['id']: c for c in data['cases']}
    case_id = st.selectbox('Case', list(cases),
                           format_func=lambda i: f"{i} · {cases[i]['grade'].get('score')} điểm · {cases[i]['result']['status']}")
    case = cases[case_id]
    with st.chat_message('user'):
        st.markdown(case['question'])
        if case.get('scenario'):
            st.caption(f"scenario: {case['scenario']}")
    with st.chat_message('assistant'):
        render_result(case['result'], case['grade'])
    with st.expander('Expected (đáp án mong đợi của case)'):
        st.write(case.get('expected'))
    st.stop()

# ---------- Hỏi agent ----------
st.title('Status Agent')
st.caption('ReAct loop tự viết · mỗi câu hỏi là một lần chạy `run_agent` độc lập')

if 'messages' not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg['role']):
        if msg['role'] == 'user':
            st.markdown(msg['content'])
            st.caption(msg['meta'])
        else:
            render_result(msg['result'])

prompt = st.chat_input('Ví dụ: GitHub Actions có đang bị sự cố không?', submit_mode='disable')
if prompt:
    meta = f"{provider_name}/{model} · {mode} · " + (f"bonus/{scenario}" if bonus else ('tool lỗi' if fail else 'core')) + f" · max_steps={max_steps}"
    st.session_state.messages.append({'role': 'user', 'content': prompt, 'meta': meta})
    with st.chat_message('user'):
        st.markdown(prompt)
        st.caption(meta)

    with st.chat_message('assistant'):
        try:
            with st.spinner('Agent đang chạy...'):
                provider = Provider(provider_name, model or None)
                tools = BonusTools(mode, scenario) if bonus else StatusTools(mode, fail=fail)
                result = run_agent(prompt, provider, tools, max_steps=int(max_steps))
        except (ValueError, RuntimeError, NotImplementedError) as exc:
            st.error(f'Lỗi cấu hình: {exc}')
            st.session_state.messages.pop()
            st.stop()
        render_result(result)
    st.session_state.messages.append({'role': 'assistant', 'result': result})
