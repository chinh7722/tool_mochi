"""Literal, accent-insensitive job identity searches shared by SQL and desktop."""
import unicodedata


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFD',str(value or '').casefold().replace('đ','d'))
        if not unicodedata.combining(c))


def search_query(value):
    if not isinstance(value,str) or len(value)>160:
        raise ValueError('Từ khóa tìm kiếm tối đa 160 ký tự.')
    return normalized(value.strip())


def matches_job(row,query):
    needle=search_query(query)
    return any(needle in normalized(row.get(key)) for key in (
        'job_id','invite_code','code','creator_name','creator_chat_id','name','chat_id'))


def sql_search(db,query):
    db.create_function('job_search_text',1,normalized,deterministic=True)
    # Bound parameters and instr keep %/_/quotes literal rather than SQL wildcards.
    return (" AND (instr(job_search_text(j.job_id),?)>0 OR instr(job_search_text(j.code),?)>0"
        " OR instr(job_search_text(u.name),?)>0 OR instr(job_search_text(j.chat_id),?)>0)",
        (search_query(query),)*4)
