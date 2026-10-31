import io
import json
import subprocess
from unittest.mock import Mock
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from signalloop_api import portfolio_pdf as pdf


def document(text=None, pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        if text:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 40 700 Td (' + text + ') Tj ET').encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('test-password')
    buf = io.BytesIO(); writer.write(buf)
    return buf.getvalue()


def test_real_pdf_text():
    text = 'Alex Morgan - Campus Connect - Python event service - FastAPI'
    assert text in pdf.extract_pdf(document(text))


@pytest.mark.parametrize('data,match', [(b'not a PDF','valid PDF'), (b'%PDF-'+b'x'*pdf.MAX_BYTES,'5 MB'), (document(), 'no readable text'), (document(pages=11),'up to 10 pages'), (document(encrypted=True),'unlocked')])
def test_bad_pdf(data,match):
    with pytest.raises(ValueError, match=match): pdf.extract_pdf(data)


def test_timeout(monkeypatch):
    def timeout(*a,**kw): raise subprocess.TimeoutExpired('parser',12)
    monkeypatch.setattr(pdf.subprocess,'run',timeout)
    with pytest.raises(ValueError,match='too long'): pdf.extract_pdf(b'%PDF-1.7')


def test_draft_topics_never_inferred(monkeypatch):
    monkeypatch.setattr(pdf.settings,'featherless_api_key','test')
    draft={'name':'Alex','domain':'Web','projects':[],'skills':['Python'],'dsa_topics':['arrays']}
    response=Mock();response.json.return_value={'choices':[{'message':{'content':json.dumps(draft)}}]}
    monkeypatch.setattr(pdf.httpx,'post',lambda *a,**kw:response)
    result=pdf.draft_from_text('untrusted portfolio')
    assert result['dsa_topics']==[]
    assert result['projects']==[{'name':'','description':'','tech_stack':[]}]


def test_invalid_model_response(monkeypatch):
    monkeypatch.setattr(pdf.settings,'featherless_api_key','test')
    response=Mock();response.json.return_value={'choices':[{'message':{'content':'not json'}}]}
    monkeypatch.setattr(pdf.httpx,'post',lambda *a,**kw:response)
    with pytest.raises(ValueError,match='could not import'): pdf.draft_from_text('text')


def test_upload_endpoint(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from signalloop_api.portfolio import router
    app=FastAPI();app.include_router(router)
    monkeypatch.setattr(pdf,'draft_from_text',lambda text: {'name':'Alex','dsa_topics':[]})
    with TestClient(app) as client:
        result=client.post('/portfolio/import-pdf',content=document('Alex Morgan - Campus Connect Python project portfolio'))
        assert result.status_code==200
        assert result.json()['portfolio']['dsa_topics']==[]
        assert client.post('/portfolio/import-pdf',content=b'bad').status_code==422
        assert client.post('/portfolio/import-pdf',content=b'x'*(pdf.MAX_BYTES+1)).status_code==413


@pytest.fixture(autouse=True)
def isolated_usage_budget(monkeypatch,session_factory):
    monkeypatch.setattr('signalloop_api.portfolio_limits.SessionLocal',session_factory)
