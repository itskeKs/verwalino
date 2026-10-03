"""Scoped derivatives contain only selected rendered appearance, never source objects."""
import hashlib
import io
import shutil

import pdfplumber
import pytest
from reportlab.pdfgen import canvas


def original_pdf():
    buffer=io.BytesIO();pdf=canvas.Canvas(buffer,pagesize=(240,180),invariant=1)
    pdf.setTitle('PRIVATE_METADATA');pdf.setAuthor('PRIVATE_AUTHOR')
    for color,label in [('green','SELECTED PAGE'),('red','OTHER_PRIVATE_PAGE')]:
        pdf.setFillColor(color);pdf.rect(0,0,240,180,fill=1,stroke=0)
        pdf.setFillColor('black');pdf.drawString(15,150,label)
        hidden=pdf.beginText(15,120);hidden.setTextRenderMode(3);hidden.textOut('HIDDEN_PRIVATE_TEXT');pdf.drawText(hidden)
        pdf.linkURL('https://private.invalid/secret',(0,0,50,50),relative=0)
        pdf.showPage()
    pdf.save();return buffer.getvalue()


def test_actual_isolated_extract_keeps_selected_pixels_without_source_objects():
    from app.core.visual_extract import run_isolated_visual_extract
    assert shutil.which('pdftoppm'),'Local renderer prerequisite unavailable'
    source=original_pdf();digest=hashlib.sha256(source).hexdigest()
    result=run_isolated_visual_extract(source,digest,1,1,2)
    assert result.status=='succeeded',result.error_code
    assert result.source_sha256==digest and result.page_start==result.page_end==1
    assert result.pdf_bytes and hashlib.sha256(result.pdf_bytes).hexdigest()==result.content_sha256
    assert result.content_byte_size==len(result.pdf_bytes) and result.output_page_count==1
    for token in [b'PRIVATE_METADATA',b'PRIVATE_AUTHOR',b'HIDDEN_PRIVATE_TEXT',b'OTHER_PRIVATE_PAGE',b'private.invalid']:
        assert token not in result.pdf_bytes
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        assert len(pdf.pages)==1
        assert pdf.pages[0].extract_text()=='Visual extract - source page 1'
        assert not pdf.pages[0].annots
        assert not any(key in pdf.doc.catalog for key in ('Names','OpenAction','AcroForm','Outlines'))
        image=pdf.pages[0].images[0]
        from PIL import Image
        pixels=Image.frombytes('RGB',image['srcsize'],image['stream'].get_data())
        red,green,blue=pixels.getpixel((pixels.width//2,pixels.height//2))
        assert green>red and green>blue
    assert hashlib.sha256(source).hexdigest()==digest


@pytest.mark.parametrize('change',['hash','count','range','boolean','oversized','format'])
def test_invalid_binding_returns_no_partial_pdf(change):
    from app.core.visual_extract import run_isolated_visual_extract,MAX_INPUT_BYTES
    source=original_pdf();digest=hashlib.sha256(source).hexdigest();start,end,count=1,1,2
    if change=='hash':digest='0'*64
    elif change=='count':count=3
    elif change=='range':end=3
    elif change=='boolean':start=True
    elif change=='oversized':source=b'%PDF-'+b'x'*MAX_INPUT_BYTES;digest=hashlib.sha256(source).hexdigest()
    else:source=b'<invoice/>';digest=hashlib.sha256(source).hexdigest()
    result=run_isolated_visual_extract(source,digest,start,end,count)
    assert result.status!='succeeded' and result.pdf_bytes is None and result.content_sha256 is None


def test_timeout_discards_all_output_and_child_environment_excludes_credentials(monkeypatch):
    from app.core.visual_extract import child_environment,run_isolated_visual_extract
    monkeypatch.setenv('USER','must-not-propagate');monkeypatch.setenv('HV_DATABASE_URL','must-not-propagate')
    monkeypatch.setenv('HV_OPENAI_API_KEY','must-not-propagate')
    assert not {'USER','HV_DATABASE_URL','HV_OPENAI_API_KEY'}.intersection(child_environment())
    source=original_pdf()
    result=run_isolated_visual_extract(source,hashlib.sha256(source).hexdigest(),1,1,2,timeout=0.001)
    assert result.status=='timeout' and result.pdf_bytes is None


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_cropbox_rotation_visible_annotations_and_attachments(rotation):
    import pikepdf
    from app.core.visual_extract import run_isolated_visual_extract
    buffer=io.BytesIO();pdf=canvas.Canvas(buffer,pagesize=(240,180),invariant=1)
    pdf.setFillColorRGB(1,0,0);pdf.rect(0,0,240,180,stroke=0,fill=1)
    pdf.setFillColorRGB(0,1,0);pdf.rect(40,30,160,120,stroke=0,fill=1)
    pdf.drawString(2,5,'OUTSIDE_CROP_PRIVATE');pdf.showPage();pdf.save()
    with pikepdf.Pdf.open(io.BytesIO(buffer.getvalue())) as source:
        page=source.pages[0]
        page.CropBox=pikepdf.Array([40,30,200,150]);page.Rotate=rotation
        source.attachments['private.xml']=b'<private>ATTACHMENT_PRIVATE_VALUE</private>'
        appearance=source.make_stream(b'q 0 0 1 rg 0 0 40 30 re f Q')
        appearance.Type=pikepdf.Name.XObject;appearance.Subtype=pikepdf.Name.Form
        appearance.BBox=pikepdf.Array([0,0,40,30])
        page.Annots=pikepdf.Array([source.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,Subtype=pikepdf.Name.Square,Rect=pikepdf.Array([80,60,120,90]),
            F=4,AP=pikepdf.Dictionary(N=appearance)))])
        source_bytes=io.BytesIO();source.save(source_bytes)
    data=source_bytes.getvalue()
    result=run_isolated_visual_extract(data,hashlib.sha256(data).hexdigest(),1,1,1)
    assert result.status=='succeeded',result.error_code
    with pikepdf.Pdf.open(io.BytesIO(result.pdf_bytes)) as output:
        assert len(output.pages)==1 and not list(output.attachments)
        assert '/Annots' not in output.pages[0] and '/Names' not in output.Root
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as output:
        image=output.pages[0].images[0]
        from PIL import Image
        pixels=Image.frombytes('RGB',image['srcsize'],image['stream'].get_data())
        colors=pixels.getcolors(pixels.width*pixels.height)
        assert sum(n for n,(red,green,blue) in colors if blue>200 and red<20 and green<20)>100
        assert sum(n for n,(red,green,blue) in colors if red>200 and green<20 and blue<20)==0
        assert (pixels.width>pixels.height)==(rotation%180==0)


@pytest.mark.parametrize('case',['inverted_crop','crop_outside_media','rotation','user_unit'])
def test_ambiguous_page_geometry_is_rejected(case):
    import pikepdf
    from app.core.visual_extract import run_isolated_visual_extract
    with pikepdf.Pdf.open(io.BytesIO(original_pdf())) as source:
        page=source.pages[0]
        if case=='inverted_crop':page.CropBox=pikepdf.Array([150,150,10,10])
        elif case=='crop_outside_media':page.CropBox=pikepdf.Array([-20,-20,260,200])
        elif case=='rotation':page.Rotate=45
        else:page.UserUnit=2
        output=io.BytesIO();source.save(output)
    data=output.getvalue();result=run_isolated_visual_extract(data,hashlib.sha256(data).hexdigest(),1,1,2)
    assert result.status!='succeeded' and result.pdf_bytes is None


def test_generation_enforces_aggregate_pixels_and_output_limit_before_writing(monkeypatch,tmp_path):
    from app.core import visual_extract
    data=original_pdf();digest=hashlib.sha256(data).hexdigest();output=tmp_path/'extract.pdf'
    with monkeypatch.context() as patch:
        patch.setattr(visual_extract,'MAX_TOTAL_PIXELS',1)
        with pytest.raises(ValueError,match='aggregate_pixel_limit'):
            visual_extract._render(data,digest,1,1,2,output)
        assert not output.exists()
    with monkeypatch.context() as patch:
        patch.setattr(visual_extract,'MAX_OUTPUT_BYTES',100)
        with pytest.raises(ValueError,match='output_size_limit'):
            visual_extract._render(data,digest,1,1,2,output)
        assert output.stat().st_size<=100


def test_private_scratch_is_parent_owned(monkeypatch, tmp_path):
    from app.core import visual_extract
    original = visual_extract.tempfile.TemporaryDirectory
    parents = []
    def temporary_directory(*args, **kwargs):
        parents.append(kwargs.get('dir'))
        return original(*args, **kwargs)
    monkeypatch.setattr(visual_extract.tempfile, 'TemporaryDirectory', temporary_directory)
    source = original_pdf()
    visual_extract._render(source, hashlib.sha256(source).hexdigest(), 1, 1, 2, tmp_path / 'extract.pdf')
    assert parents == [tmp_path]
    assert list(tmp_path.iterdir()) == [tmp_path / 'extract.pdf']


def test_timeout_removes_scratch_created_by_killed_child(monkeypatch):
    from pathlib import Path
    import sys
    from app.core import visual_extract
    original = visual_extract.subprocess.Popen
    directories = []
    def launch(args, **kwargs):
        directory = Path(args[-1]).parent
        directories.append(directory)
        script = ('import pathlib,sys,time; p=pathlib.Path(sys.argv[1])/"pages-test"; '
                  'p.mkdir(); (p/"source.pdf").write_bytes(b"private source"); '
                  '(p/"page.png").write_bytes(b"private pixels"); '
                  'print("ready",flush=True); time.sleep(60)')
        process = original([sys.executable, '-c', script, str(directory)], **kwargs)
        assert process.stdout.readline() == b'ready\n'
        assert (directory / 'pages-test' / 'source.pdf').exists()
        return process
    monkeypatch.setattr(visual_extract.subprocess, 'Popen', launch)
    source = original_pdf()
    result = visual_extract.run_isolated_visual_extract(source, hashlib.sha256(source).hexdigest(), 1, 1, 2, timeout=0.01)
    assert result.status == 'timeout' and result.pdf_bytes is None
    assert directories and all(not directory.exists() for directory in directories)


@pytest.mark.parametrize('mutation', [
    'list', 'boolean_count', 'unknown_status', 'unknown_error', 'list_error',
    'extra_key', 'box', 'annotations', 'text_layer', 'version_control', 'version_empty', 'profile_extra',
])
def test_public_entry_rejects_malformed_child_protocol(monkeypatch, mutation):
    import json
    from pathlib import Path
    from app.core import visual_extract
    source = original_pdf()
    digest = hashlib.sha256(source).hexdigest()
    pdf = b'%PDF-1.4\ncontrolled protocol fixture'
    result = {'status': 'succeeded', 'version': visual_extract.VERSION,
              'source_sha256': digest, 'page_start': 1, 'page_end': 1, 'verified_page_count': 2,
              'output_page_count': 1, 'content_sha256': hashlib.sha256(pdf).hexdigest(),
              'content_byte_size': len(pdf), 'renderer_profile': {
                  **visual_extract._fixed_profile(), 'rasterizer': 'poppler test',
                  'pdf_writer': 'test', 'image_library': 'test'}}
    if mutation == 'list': result = []
    elif mutation == 'boolean_count': result['output_page_count'] = True
    elif mutation == 'unknown_status': result['status'] = 'pending'
    elif mutation == 'unknown_error': result = {'status': 'failed', 'error_code': 'private arbitrary text'}
    elif mutation == 'list_error': result = {'status': 'failed', 'error_code': ['renderer_failed']}
    elif mutation == 'extra_key': result['private'] = 'unexpected'
    elif mutation == 'box': result['renderer_profile']['box'] = 'MediaBox'
    elif mutation == 'annotations': result['renderer_profile']['annotations'] = 'hidden'
    elif mutation == 'text_layer': result['renderer_profile']['text_layer'] = 0
    elif mutation == 'version_control': result['renderer_profile']['rasterizer'] = 'bad\nversion'
    elif mutation == 'version_empty': result['renderer_profile']['pdf_writer'] = ''
    else: result['renderer_profile']['private'] = 'unexpected'
    class Child:
        pid = 999999999
        returncode = 0
        def __init__(self, args, **kwargs):
            Path(args[-1]).write_bytes(pdf)
        def communicate(self, *args, **kwargs):
            return json.dumps(result).encode(), None
    monkeypatch.setattr(visual_extract.subprocess, 'Popen', Child)
    monkeypatch.setattr(visual_extract.os, 'killpg', lambda *args: None)
    outcome = visual_extract.run_isolated_visual_extract(source, digest, 1, 1, 2)
    assert outcome.status == 'failed'
    assert outcome.pdf_bytes is None and outcome.content_sha256 is None
