import copy, hashlib, json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
 import grow_static as g
except ImportError:
 g=None

class PublisherTests(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(g,'publisher implementation must exist')
  self.a=json.loads((Path(__file__).resolve().parents[2]/'fixtures/sample-a.json').read_text(encoding='utf-8'))
  self.b=json.loads((Path(__file__).resolve().parents[2]/'fixtures/sample-b.json').read_text(encoding='utf-8'))
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'generator')
  self.addCleanup(self.tmp.cleanup); self.out=Path(self.tmp.name)
 def test_publish_history_exact_hash_and_idempotency(self):
  first=g.build_release(self.a,self.out)
  self.assertEqual(first,g.build_release(self.a,self.out))
  g.build_release(self.b,self.out); g.validate_release(self.out)
  history=json.loads((self.out/'history/index.json').read_text())
  self.assertEqual(2,len(history['releases']))
  self.assertEqual(first['sha256'],hashlib.sha256((self.out/first['article_path']).read_bytes()).hexdigest())
 def test_invalid_publication_preserves_latest(self):
  g.build_release(self.a,self.out); before=(self.out/'latest.json').read_bytes()
  for mutate in [lambda x:x.update(schema_version='9'),lambda x:x['sections']['news'].update(claim_ids=['missing']),lambda x:x.update(published_at='not-date'),lambda x:x.update(extra=True),lambda x:x['sources'][0].update(url='http://example.org')]:
   bad=copy.deepcopy(self.b); mutate(bad)
   with self.assertRaises(ValueError):g.build_release(bad,self.out)
   self.assertEqual(before,(self.out/'latest.json').read_bytes())
 def test_revisions_immutable(self):
  g.build_release(self.a,self.out); self.a['title']='changed'
  with self.assertRaises(ValueError):g.build_release(self.a,self.out)
  g.validate_release(self.out)
 def test_tampering_and_bad_hash_rejected(self):
  release=g.build_release(self.a,self.out)
  path=self.out/release['article_path']; original=path.read_bytes(); path.write_bytes(original+b' ')
  with self.assertRaises(ValueError):g.validate_release(self.out)
  path.write_bytes(original)
  pointer=json.loads((self.out/'latest.json').read_text());pointer['sha256']='0'*64
  (self.out/'latest.json').write_text(json.dumps(pointer))
  with self.assertRaises(ValueError):g.validate_release(self.out)
 def test_bad_source_reference_rejected(self):
  self.a['claims'][0]['evidence'][0]['source_id']='missing'
  with self.assertRaises(ValueError):g.validate_edition(self.a)


class CommitFailureTests(PublisherTests):
 def test_manifest_write_failure_preserves_valid_release(self):
  from unittest.mock import patch
  g.build_release(self.a,self.out)
  before=(self.out/'latest.json').read_bytes();history=(self.out/'history/index.json').read_bytes()
  original=g._atomic
  def fail_latest(path,data):
   if path.name=='latest.json':raise OSError('injected disk failure')
   return original(path,data)
  with patch.object(g,'_atomic',side_effect=fail_latest):
   with self.assertRaises(OSError):g.build_release(self.b,self.out)
  self.assertEqual(before,(self.out/'latest.json').read_bytes())
  self.assertEqual(history,(self.out/'history/index.json').read_bytes())
  g.validate_release(self.out)
 def test_semantic_invalid_dates_and_duplicates(self):
  for mutate in [lambda x:x.update(edition_id='2026-02-30'),lambda x:x.update(generated_at='2026-10-14T25:00:00Z'),lambda x:x['sources'].append(copy.deepcopy(x['sources'][0])),lambda x:x['sections']['technology'].update(limitations='wrong type'),lambda x:x['sections']['practice'].update(verification_status='tested')]:
   bad=copy.deepcopy(self.a);mutate(bad)
   with self.assertRaises(ValueError):g.validate_edition(bad)

class ReviewHardeningTests(PublisherTests):
 def test_bounds_and_unsupported_tested(self):
  for mutate in [lambda x:x.update(title='a'*161),lambda x:x.update(summary='a'*501),lambda x:x['sections']['news'].update(body='a'*12001),lambda x:x['sections']['practice'].update(steps=['a']*101),lambda x:x.update(is_sample=False) or x['sections']['practice'].update(verification_status='tested')]:
   bad=copy.deepcopy(self.a);mutate(bad)
   with self.assertRaises(ValueError):g.validate_edition(bad)
 def test_time_consistency(self):
  for mutate in [lambda x:x.update(generated_at='2026-10-15T12:00:00+08:00'),lambda x:x['sections']['news'].update(event_at='2026-10-15T12:00:00+08:00'),lambda x:x['sources'][0].update(retrieved_at='2026-10-15T12:00:00+08:00'),lambda x:x['sources'][0].update(published_at='2026-10-15T12:00:00+08:00'),lambda x:x['sections']['github'].update(checked_at='2026-10-15T12:00:00+08:00')]:
   bad=copy.deepcopy(self.a);mutate(bad)
   with self.assertRaises(ValueError):g.validate_edition(bad)
 def test_duplicate_json_keys(self):
  path=self.out/'duplicate.json';path.write_text('{"schema_version":"1.0","schema_version":"9"}')
  with self.assertRaises(ValueError):g._read(path)
 def test_no_publication_or_revision_regression(self):
  self.a['revision']=3;g.build_release(self.a,self.out)
  lower=copy.deepcopy(self.a);lower['revision']=2
  with self.assertRaises(ValueError):g.build_release(lower,self.out)
  g.build_release(self.b,self.out)
  older=copy.deepcopy(self.a);older['revision']=4
  with self.assertRaises(ValueError):g.build_release(older,self.out)


class HistoryRegressionTests(PublisherTests):
 def test_history_cannot_reverse_editions_with_reassigned_sequences(self):
  g.build_release(self.a,self.out);g.build_release(self.b,self.out)
  path=self.out/'history/index.json';history=json.loads(path.read_text())
  history['releases'].reverse()
  for i,release in enumerate(history['releases'],1):release['release_sequence']=i
  path.write_text(json.dumps(history));(self.out/'latest.json').write_text(json.dumps(history['releases'][-1]))
  with self.assertRaises(ValueError):g.validate_release(self.out)
 def test_history_ahead_of_latest_remains_valid(self):
  first=g.build_release(self.a,self.out);g.build_release(self.b,self.out)
  (self.out/'latest.json').write_text(json.dumps(first))
  self.assertEqual(first,g.validate_release(self.out))
 def test_history_same_edition_revision_regression(self):
  self.a['revision']=2;g.build_release(self.a,self.out)
  self.a['revision']=3;g.build_release(self.a,self.out)
  path=self.out/'history/index.json';history=json.loads(path.read_text());history['releases'].reverse()
  for i,release in enumerate(history['releases'],1):release['release_sequence']=i
  path.write_text(json.dumps(history));(self.out/'latest.json').write_text(json.dumps(history['releases'][-1]))
  with self.assertRaises(ValueError):g.validate_release(self.out)

if __name__=='__main__':unittest.main()
