import unittest
from desktop_status import reduce_status


def snapshot(status, revision=1):
    return {'type':'snapshot','revision':revision,'conversationState':{
        'threadRuntimeStatus':status,'requests':[{'secret':'PRIVATE APPROVAL TEXT'}],
        'turnHistory':{'secret':'PRIVATE TRANSCRIPT'}}}


class DesktopStatusTests(unittest.TestCase):
    def test_snapshot_retains_only_status(self):
        result=reduce_status(None,snapshot({'type':'active','activeFlags':['waitingOnApproval']}))
        self.assertEqual(result,{'revision':1,'status':{'type':'active','activeFlags':['waitingOnApproval']}})
        self.assertNotIn('PRIVATE',str(result))

    def test_approval_response_clears_waiting_flag(self):
        initial=reduce_status(None,snapshot({'type':'active','activeFlags':['waitingOnApproval']}))
        result=reduce_status(initial,{'type':'patches','baseRevision':1,'revision':2,
            'patches':[{'op':'remove','path':['threadRuntimeStatus','activeFlags',0]}]})
        self.assertEqual(result['status'],{'type':'active','activeFlags':[]})
        self.assertEqual(initial['status']['activeFlags'],['waitingOnApproval'])

    def test_unrelated_progress_preserves_waiting(self):
        initial=reduce_status(None,snapshot({'type':'active','activeFlags':['waitingOnUserInput']}))
        result=reduce_status(initial,{'type':'patches','baseRevision':1,'revision':2,
            'patches':[{'op':'add','path':['turns',0],'value':{'text':'PRIVATE'}}]})
        self.assertEqual(result['status'],initial['status'])
        self.assertNotIn('PRIVATE',str(result))

    def test_missing_revision_requires_fresh_snapshot(self):
        initial=reduce_status(None,snapshot({'type':'active','activeFlags':[]}))
        self.assertIsNone(reduce_status(initial,{'type':'patches','baseRevision':3,'revision':4,'patches':[]}))

    def test_completion_replaces_active_status(self):
        initial=reduce_status(None,snapshot({'type':'active','activeFlags':['waitingOnApproval']}))
        result=reduce_status(initial,{'type':'patches','baseRevision':1,'revision':2,
            'patches':[{'op':'replace','path':['threadRuntimeStatus'],'value':{'type':'idle'}}]})
        self.assertEqual(result['status']['type'],'idle')

    def test_invalid_flag_patch_is_unavailable(self):
        initial=reduce_status(None,snapshot({'type':'active','activeFlags':[]}))
        self.assertIsNone(reduce_status(initial,{'type':'patches','baseRevision':1,'revision':2,
            'patches':[{'op':'remove','path':['threadRuntimeStatus','activeFlags',4]}]}))


if __name__=='__main__':
    unittest.main()
