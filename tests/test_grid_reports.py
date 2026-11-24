"""Grid reports and portable replay through the existing public validate CLI."""
import json,unittest,importlib.util
from pathlib import Path
from decimal import Decimal
from algoLibs.market_data.types import decode
import test_grid_validation as grid_fixture
import test_portable_validation as portable_fixture


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class GridReportTests(unittest.TestCase):
    setUp=grid_fixture.GridValidationTests.setUp
    inputs=grid_fixture.GridValidationTests.inputs
    command=grid_fixture.GridValidationTests.command
    study=grid_fixture.GridValidationTests.study
    replay=portable_fixture.PortableValidationTests.replay

    def test_grid_parameters_and_canonical_metrics_are_visible_and_replay_offline(self):
        from openpyxl import load_workbook
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'vol_target':['.25','.75'],'smoothing_span':[3,6]}
        self.plan_path.write_text(json.dumps(self.plan))
        original,report,preview=self.study('original','--duckdb',self.path,'--portable','--trace')
        search=report['candidate_search']
        self.assertEqual(search['declaration'],self.plan['selection']['search'])
        self.assertEqual(search['candidates'],preview['candidates'])
        self.assertEqual(search['summary']['candidate_count'],4)
        self.assertEqual(search['summary']['planned_evaluation_count'],5)
        self.assertEqual(search['summary']['ranking_phase'],'training')
        selection=report['selections']['fold-001']
        chosen=next(c for c in search['candidates'] if c['id']==selection['selected_candidate'])
        self.assertEqual(selection['selected_parameters'],chosen['parameters'])
        book=load_workbook(original/'validation.xlsx');self.addCleanup(book.close)
        self.assertIn('Grid summary',book.sheetnames);self.assertIn('Candidates',book.sheetnames)
        rows=list(book['Training'].values);headers=list(rows[0]);self.assertEqual(len(rows),5)
        self.assertIn('parameter.vol_target',headers);self.assertIn('parameter.smoothing_span',headers)
        for excel,trial in zip(rows[1:],selection['trials']):
            self.assertEqual(excel[headers.index('phase')],'training')
            self.assertAlmostEqual(excel[headers.index('parameter.vol_target')],float(trial['parameters']['vol_target']))
            self.assertEqual(excel[headers.index('parameter.smoothing_span')],trial['parameters']['smoothing_span'])
            for metric in ('score','total_fees','turnover','max_drawdown','complete_day_count'):
                expected=trial['score'] if metric=='score' else trial['metrics'][metric]
                self.assertAlmostEqual(excel[headers.index(metric)],float(expected))
        cell=book['Training'].cell(2,headers.index('report')+1)
        self.assertTrue(cell.hyperlink);self.assertTrue((original/cell.hyperlink.target).is_file())
        selected=list(book['Selection'].values);selected_headers=list(selected[0])
        self.assertEqual(selected[1][selected_headers.index('phase')],'selected_test')
        self.assertEqual(selected[1][selected_headers.index('fallback_used')],False)
        self.assertIn('parameter.vol_target',selected_headers)
        self.assertEqual(len(list(book['Candidates'].values)),5)
        self.path.rename(self.root/'unavailable.duckdb')
        output=self.root/'replay';replayed=self.replay(original,output)
        self.assertEqual(replayed['candidate_search'],search)
        self.assertEqual(replayed['selections']['fold-001']['selected_parameters'],selection['selected_parameters'])
        self.assertGreater(replayed['selections']['fold-001']['performed_at'],selection['performed_at'])
        for run in original.glob('cases/**/run'):
            for name in ('result.json','targets.json','report.json','signals.json'):
                self.assertEqual((run/name).read_bytes(),(output/run.relative_to(original)/name).read_bytes())
        access=decode(json.loads((output/'cases/fold-001/run/data-access.json').read_text()))
        self.assertIn('DataRequest.accept',access['route']);self.assertTrue(access['reads'])
        research=decode(json.loads((output/'cases/fold-001/run/research.json').read_text()))
        self.assertEqual(research['run_details']['context']['storage_input']['backend'],'native_snapshot')
        self.assertFalse(report['folds'][0]['eligible'])
        self.assertEqual(report['trial_history']['status'],'incomplete_after_training_selection')

    def test_reports_match_explicit_candidates_and_keep_test_phases_and_qualifications(self):
        from openpyxl import load_workbook
        self.inputs();self.database.close()
        self.plan.update(capital_policy='continuous_account',max_evaluations=6)
        self.plan['selection']['search']['parameters']={'vol_target':['.25','.75'],'smoothing_span':[3,6],'warmup_bars':[512]}
        self.plan_path.write_text(json.dumps(self.plan))
        grid,first,preview=self.study('grid','--duckdb',self.path,'--portable')
        book=load_workbook(grid/'validation.xlsx');self.addCleanup(book.close)
        details=dict(book['Run details'].values)
        self.assertEqual(details['trial_history.status'],'incomplete_after_training_selection')
        self.assertEqual(details['trial_history.effective_independent_trials'],'N/A')
        self.assertIn('dependent',details['trial_history.convention'])
        self.assertEqual(len(list(book['Training'].values)),5)
        self.assertEqual(len(list(book['Selection'].values)),2)
        self.assertEqual(len(list(book['Folds'].values)),2)
        self.assertIn('Continuous account',book.sheetnames)
        selected=list(book['Selection'].values);headers=list(selected[0])
        report_cell=book['Selection'].cell(2,headers.index('report')+1)
        self.assertTrue(report_cell.hyperlink);self.assertTrue((grid/report_cell.hyperlink.target).exists())
        self.assertIn('parameter.warmup_bars',headers)
        self.assertEqual(first['candidate_search']['parameter_names'],['smoothing_span','vol_target','warmup_bars'])
        self.assertEqual(first['candidate_search']['summary']['selection_scope'],'per_fold')
        identities={(c['parameters']['vol_target'],c['parameters']['smoothing_span']):c['id'] for c in preview['candidates']}
        self.plan['selection'].pop('search')
        self.plan['selection']['candidates']=[dict(id=identities[(Decimal(vol),smooth)],recipe_overrides=dict(vol_target=vol,smoothing_span=smooth,warmup_bars=512))
            for vol,smooth in [('.75',6),('.25',6),('.75',3),('.25',3)]]
        self.plan_path.write_text(json.dumps(self.plan))
        explicit,second,_=self.study('explicit','--duckdb',self.path,'--portable')
        fields=('score','metrics','parameters','eligible','ineligible_reason')
        a={t['case_id']:{k:t[k] for k in fields} for t in first['selections']['fold-001']['trials']}
        b={t['case_id']:{k:t[k] for k in fields} for t in second['selections']['fold-001']['trials']}
        self.assertEqual(a,b)
        self.assertEqual(first['selections']['fold-001']['selected_candidate'],second['selections']['fold-001']['selected_candidate'])
        self.assertEqual(first['continuous']['metrics'],second['continuous']['metrics'])
        for folder in grid.glob('cases/**/run'):
            for name in ('result.json','report.json','targets.json'):
                self.assertEqual((folder/name).read_bytes(),(explicit/folder.relative_to(grid)/name).read_bytes())
        self.path.rename(self.root/'unavailable.duckdb')
        replayed=self.replay(explicit,self.root/'explicit-replay')
        self.assertEqual(replayed['candidate_search'],second['candidate_search'])
        self.assertEqual(replayed['continuous']['metrics'],second['continuous']['metrics'])

    def reseal(self,study):
        """Recompute public integrity receipts to test semantic checks beyond hashes."""
        import hashlib
        from algoLibs.market_data.types import encode
        path=study/'manifest.json';manifest=decode(json.loads(path.read_text()))
        for name in manifest['files']:manifest['files'][name]=hashlib.sha256((study/name).read_bytes()).hexdigest()
        path.write_text(json.dumps(encode(manifest)))
        (study/'manifest.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())

    def test_replay_rejects_resealed_changed_source_inventory_before_accounts(self):
        import io,os,zipfile
        from unittest.mock import patch
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'smoothing_span':[6]}
        self.plan_path.write_text(json.dumps(self.plan))
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        path=original/'source.zip';stream=io.BytesIO()
        with zipfile.ZipFile(path) as source,zipfile.ZipFile(stream,'w') as changed:
            for info in source.infolist():
                payload=source.read(info.filename)
                if info.filename=='algoFarmBT/backtests/validation.py':payload+=b'\n# inconsistent retained source\n'
                changed.writestr(info,payload)
        path.write_bytes(stream.getvalue());self.reseal(original)
        self.path.rename(self.root/'unavailable.duckdb')
        mkdir=os.mkdir;created=[]
        def observe(path,*args,**kwargs):
            if 'cases' in Path(path).parts:created.append(str(path))
            return mkdir(path,*args,**kwargs)
        with patch('os.mkdir',side_effect=observe):
            with self.assertRaisesRegex(ValueError,'source inventory'):
                self.replay(original,self.root/'invalid')
        self.assertEqual(created,[])
        self.assertFalse((self.root/'invalid').exists())

    def test_replay_rejects_resealed_retained_candidate_recipe_before_accounts(self):
        import os
        from unittest.mock import patch
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'smoothing_span':[6]}
        self.plan_path.write_text(json.dumps(self.plan))
        original,_,preview=self.study('original','--duckdb',self.path,'--portable')
        candidate=preview['candidates'][0]
        path=original/'common/candidates'/(candidate['id']+'.json')
        recipe=json.loads(path.read_text());recipe['vol_target']='1';path.write_text(json.dumps(recipe));self.reseal(original)
        self.path.rename(self.root/'unavailable.duckdb')
        mkdir=os.mkdir;created=[]
        def observe(path,*args,**kwargs):
            if 'cases' in Path(path).parts:created.append(str(path))
            return mkdir(path,*args,**kwargs)
        with patch('os.mkdir',side_effect=observe):
            with self.assertRaisesRegex(ValueError,'candidate recipe.*frozen'):
                self.replay(original,self.root/'invalid')
        self.assertEqual(created,[])
        self.assertFalse((self.root/'invalid').exists())

    def test_replay_reexpands_and_cross_checks_retained_candidate_metadata_before_accounts(self):
        import hashlib,os,shutil
        from unittest.mock import patch
        from algoLibs.market_data.types import encode
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'smoothing_span':[6]}
        self.plan_path.write_text(json.dumps(self.plan))
        original,_,preview=self.study('original','--duckdb',self.path,'--portable')
        identity=preview['candidates'][0]['id'];self.path.rename(self.root/'unavailable.duckdb')
        mutations=[
            ('report','validation.json',lambda d:d['candidate_search']['candidates'][0]['parameters'].update(smoothing_span=99)),
            ('case',f'cases/fold-001/training/cases/{identity}/case.json',lambda d:d['coordinates'].update(smoothing_span=99)),
            ('selected','cases/fold-001/selected-recipe.json',lambda d:d.update(vol_target='1')),
            ('selection','cases/fold-001/selection.json',lambda d:d['selected_parameters'].update(smoothing_span=99)),
            ('frozen-default','resolved-plan.json',lambda d:d['candidates'][0]['recipe'].update(rv_fast_span=73)),
            ('grid','common/plan.json',lambda d:d['selection']['search']['parameters'].update(smoothing_span=[3])),
            ('base-default','common/recipe.json',lambda d:d.update(rv_fast_span=73))]
        for label,relative,mutate in mutations:
            with self.subTest(label=label):
                source=self.root/('changed-'+label);shutil.copytree(original,source)
                path=source/relative;value=decode(json.loads(path.read_text()));mutate(value)
                # Common recipe/plan inputs use ordinary JSON; emitted artifact payloads use the existing tagged codec.
                path.write_text(json.dumps(value if relative.startswith('common/') or relative.endswith('selected-recipe.json') else encode(value)))
                if relative=='resolved-plan.json':
                    (source/'resolved-plan.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
                self.reseal(source)
                mkdir=os.mkdir;created=[]
                def observe(path,*args,**kwargs):
                    if 'cases' in Path(path).parts:created.append(str(path))
                    return mkdir(path,*args,**kwargs)
                output=self.root/('invalid-'+label)
                with patch('os.mkdir',side_effect=observe):
                    with self.assertRaisesRegex(ValueError,'candidate metadata|Replay experiment|Selected recipe'):
                        self.replay(source,output)
                self.assertEqual(created,[])
                self.assertFalse(output.exists())

    def test_failed_trials_and_operational_fallbacks_remain_visible_and_replay(self):
        from openpyxl import load_workbook
        from test_spot_inputs import T
        self.inputs();self.database.close()
        raw=json.loads(self.recipe.read_text());raw.update(regime_mode='supplied_multiplier',
            auxiliary_rules=[dict(field='regime',source_id='provided',max_age_seconds=10000000)])
        self.recipe.write_text(json.dumps(raw))
        import hashlib
        self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        self.plan['selection']['search']['parameters']={'auxiliary_rules':[
            [dict(field='regime',source_id='provided',max_age_seconds=10000000)],
            [dict(field='regime',source_id='missing',max_age_seconds=10000000)]]}
        self.plan_path.write_text(json.dumps(self.plan))
        auxiliary=self.root/'auxiliary.json';auxiliary.write_text(json.dumps([dict(symbol=s,field='regime',observed_at=T.isoformat(),
            available_at=T.isoformat(),value='.5',units='dimensionless',source_id='provided') for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]))
        original,report,_=self.study('failed','--duckdb',self.path,'--portable','--auxiliary',auxiliary)
        book=load_workbook(original/'validation.xlsx');self.addCleanup(book.close)
        rows=list(book['Training'].values);headers=list(rows[0]);self.assertEqual(len(rows),3)
        self.assertEqual([r[headers.index('status')] for r in rows[1:]],['succeeded','failed'])
        failed=book['Training'].cell(3,headers.index('artifacts')+1)
        self.assertTrue(failed.hyperlink.target.endswith('/failure.json'))
        self.assertTrue((original/failed.hyperlink.target).exists())
        self.assertIn('parameter.auxiliary_rules',headers)
        self.path.rename(self.root/'unavailable.duckdb')
        replayed=self.replay(original,self.root/'failed-replay')
        self.assertEqual(replayed['folds'][0]['status'],'failed')
        self.assertFalse((self.root/'failed-replay/cases/fold-001/run').exists())
        self.assertEqual([t['status'] for t in replayed['selections']['fold-001']['trials']],['succeeded','failed'])
        self.path=self.root/'unavailable.duckdb'
        self.inputs();self.plan['selection']['search']['parameters']={'vol_target':['.25','.75']}
        self.plan['selection']['minimum_complete_training_days']=300
        self.plan_path.write_text(json.dumps(self.plan));preview=self.root/'fallback-candidates'
        self.command(preview,'--dry-run','--duckdb',self.path,'--portable')
        definition=decode(json.loads((preview/'resolved-plan.json').read_text()))
        self.plan['selection']['no_eligible_score_fallback']=definition['candidates'][0]['id']
        self.plan_path.write_text(json.dumps(self.plan))
        fallback,baseline,_=self.study('fallback','--duckdb',self.path,'--portable')
        book=load_workbook(fallback/'validation.xlsx');self.addCleanup(book.close)
        rows=list(book['Selection'].values);headers=list(rows[0])
        self.assertTrue(rows[1][headers.index('fallback_used')])
        self.assertFalse(rows[1][headers.index('eligible')])
        self.assertEqual(rows[1][headers.index('designation')],'development')
        self.path.rename(self.root/'database-removed.duckdb')
        replayed=self.replay(fallback,self.root/'fallback-replay')
        self.assertTrue(replayed['selections']['fold-001']['fallback_used'])
        self.assertIn('operational_fallback',replayed['folds'][0]['eligibility_reasons'])
        self.assertEqual(replayed['candidate_search'],baseline['candidate_search'])

    def test_parameter_text_uses_the_existing_literal_safe_excel_exporter(self):
        from openpyxl import load_workbook
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'name':['=1+1']}
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study('literal','--duckdb',self.path)
        self.assertEqual(report['candidate_search']['candidates'][0]['parameters']['name'],'=1+1')
        book=load_workbook(output/'validation.xlsx',data_only=False);self.addCleanup(book.close)
        for sheet_name in ('Candidates','Training','Selection'):
            sheet=book[sheet_name];headers=[c.value for c in sheet[1]]
            cell=sheet.cell(2,headers.index('parameter.name')+1)
            self.assertEqual(cell.value,'=1+1');self.assertEqual(cell.data_type,'s')
        self.assertFalse(any(cell.data_type=='f' for sheet in book for row in sheet for cell in row))

    def test_grid_replays_after_registered_parquet_and_mapping_are_removed(self):
        self.inputs()
        self.plan['selection']['search']['parameters']={'smoothing_span':[6]}
        self.plan_path.write_text(json.dumps(self.plan))
        exported=self.root/'native.parquet'
        with self.database.transaction(readonly=False) as connection:
            connection.execute('COPY public.md_bar TO ? (FORMAT PARQUET)',[str(exported)])
        self.database.close()
        mapping=self.root/'parquet.json';mapping.write_text(json.dumps({'public.md_bar':[str(exported)]}))
        original,baseline,_=self.study('parquet','--parquet',mapping,'--portable')
        exported.unlink();mapping.unlink();self.path.unlink()
        output=self.root/'offline';replayed=self.replay(original,output)
        self.assertEqual(replayed['candidate_search'],baseline['candidate_search'])
        before=decode(json.loads((original/'cases/fold-001/run/research.json').read_text()))['run_details']['context']['storage_input']
        after=decode(json.loads((output/'cases/fold-001/run/research.json').read_text()))['run_details']['context']['storage_input']
        self.assertEqual(before['backend'],'duckdb_parquet')
        self.assertEqual(after['backend'],'native_snapshot')
        self.assertEqual(after['original_storage_input'],before)
        proof=decode(json.loads((output/'replay-verification.json').read_text()))
        self.assertTrue(proof['source_inventory_verified'])
        self.assertEqual(proof['status'],'verified')


    def test_replay_rejects_a_resealed_inconsistent_frozen_plan_checksum(self):
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'smoothing_span':[6]}
        self.plan_path.write_text(json.dumps(self.plan))
        original,_,_=self.study('original','--duckdb',self.path,'--portable')
        (original/'resolved-plan.sha256').write_text('0'*64+'\n');self.reseal(original)
        self.path.rename(self.root/'unavailable.duckdb')
        with self.assertRaisesRegex(ValueError,'Frozen resolved plan.*checksum'):
            self.replay(original,self.root/'invalid')
        self.assertFalse((self.root/'invalid').exists())
