# util.py

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals
import re
import time

import botocore.session
from botocore.exceptions import ClientError

def findInEvalResults(response, action, resource):
    """Given an SimulatePrincipalPolicy API response, return if a given action
    and resource are allowed. Currently only handles * resources.
    """

    for result in response['EvaluationResults']:
        if action == result['EvalActionName'] and resource == result['EvalResourceName']:
            return result['EvalDecision'] == 'allowed'
    return False


def test_node_access(iamclient, node, actionList, resourceList=None):
    """ Go through each action and resource to determine if the passed AWSNode
    has permission for the combination. Performs at least one Simulate API call
    for each action. Breaks down large resourceLists to chunks of twenty and
    calls separate Simulate API calls per chunk.

    :param botocore.client.IAM iamclient: A Botocore client that can call the AWS IAM API
    :param principalmap.awsnode.AWSNode: An AWSNode representing some principal
    :param list actionList: A list of strings for actions in AWS (service:ActionName convention)
    :param resourceList: A list of strings for ARNs to check access to (optional)
    :type resourceList: list or None
    :return A list of tuples (str, str, bool) for each action/resource/allowed combination.
    :rtype list
    :raises ValueError: if the action list is empty or larger than twenty strings
    """
    result = []
    if actionList is None or len(actionList) > 20 or len(actionList) == 0:
        raise ValueError('Parameter "actionList" needs to include at least one action, but no more than twenty.')
    if resourceList is None or len(resourceList) < 1:
        resourceList = ['*']

    for action in actionList:
        if len(resourceList) > 20:
            # Chunk resourceList into groups of twenty
            resourceListList = []
            x = 0
            y = 20
            while x != len(resourceList):
                if y > len(resourceList):
                    y = len(resourceList)
                resourceListList.append(resourceList[x:y])
                x += 20
                y += 20
                if x > len(resourceList):
                    x = len(resourceList)
            for rlist in resourceListList:
                result.extend(_test_less(iamclient, node, action, rlist))
        else:
            result.extend(_test_less(iamclient, node, action, resourceList))

    return result


def _test_less(iamclient, node, action, resourceList):
    """ (Internal) Test if a passed node can perform a given action on a list of resources."""
    result = []
    response = None
    done = False

    while not done:
        try:
            response = iamclient.simulate_principal_policy(
                PolicySourceArn=node.label,
                ActionNames=[action],
                ResourceArns=resourceList
            )
            done = True
        except ClientError as err:
            if 'Thrott' in err.response['Error']['Code']:  # should catch Throttl(e|ing) error
                print('ThrottlingException hit, pausing execution for one second.')
                time.sleep(1)
            # TODO: implement escalate and backoff behavior
            else:
                raise(err)

    if len(resourceList) > 1:
        result.extend(_extract_resource_specific_results(response))
    else:
        result.extend(_extract_results(response))

    return result


def _extract_results(response):
    """ (Internal) Create and return a tuple in a list (str, str, bool) for action, resource, and allowed.
    Used for when only one resource (or wildcard) is passed in a Simulate API call.
    """
    result = []
    for evalresult in response['EvaluationResults']:
        result.append(
            (evalresult['EvalActionName'], evalresult['EvalResourceName'], evalresult['EvalDecision'] == 'allowed')
        )
    return result


def _extract_resource_specific_results(response):
    """ (Internal) Create and return tuples in a list (str, str, bool) for action, resource, and allowed.
    Used for when more than one resource (ARN) is specified for a Simulate API call.
    """
    result = []
    for evalresult in response['EvaluationResults']:
        action = evalresult['EvalActionName']
        for resourcespecificresult in evalresult['ResourceSpecificResults']:
            result.append(
                (action, resourcespecificresult['EvalResourceName'], resourcespecificresult['EvalResourceDecision'] == 'allowed')
            )
    return result


def testMassPass(iamclient, passer, candidates, service):
    """Performs mass-testing of iam:PassRole (multiple target roles, etc.) and
    returns a list of AWSNode that can be passed.
    """

    if len(candidates) == 0:
        return []
    results = []

    if len(candidates) > 20:
        roleListList = []
        x = 0
        y = 20
        while x != len(candidates):
            if y > len(candidates):
                y = len(candidates)
            roleListList.append(candidates[x:y])
            x += 20
            y += 20
            if x > len(candidates):
                x = len(candidates)
        for rolelist in roleListList:
            results.extend(_test_less_pass(iamclient, passer, rolelist, service))
    else:
        results.extend(_test_less_pass(iamclient, passer, candidates, service))

    return results

def _test_less_pass(iamclient, passer, candidates, service):
    """(Internal) Return a list of AWSNode for roles that can be passed to a service 
    by a given principal. Assumes the candidate list is of length 20 or less.
    """
    result = []
    arnlist = []
    response = None
    done = False

    context_entries = [{
        'ContextKeyName': 'iam:PassedToService',
        'ContextKeyValues': [service],
        'ContextKeyType': 'string'
    }]

    for candidate in candidates:
        arnlist.append(candidate.label)

    while not done:
        try:
            response = iamclient.simulate_principal_policy(
                PolicySourceArn=passer.label,
                ActionNames=['iam:PassRole'],
                ResourceArns=arnlist,
                ContextEntries=context_entries
            )
            done = True
        except ClientError as err:
            if 'Thrott' in err.response['Error']['Code']:  # should catch Throttl(e|ing) error
                print('ThrottlingException hit, pausing execution for one second.')
                time.sleep(1)
            # TODO: implement escalate and backoff behavior
            else:
                raise(err)

    result.extend(_extractPassResults(response, candidates))
    return result

def _extractPassResults(response, candidates):
    result = []
    for candidate in candidates:
        for rsr in response['EvaluationResults'][0]['ResourceSpecificResults']:
            if candidate.label == rsr['EvalResourceName'] and rsr['EvalResourceDecision'] == 'allowed':
                result.append(candidate)
    return result


# For testing actions that require iam:PassRole permission, handles
# the iam:PassedToService context entry
def testPassRole(iamclient, passer, passed, targetservice):
    context_response = iamclient.get_context_keys_for_principal_policy(PolicySourceArn=passer.label)
    context_entries = []
    if 'iam:PassedToService' in context_response['ContextKeyNames']:
        context_entries.append({
            'ContextKeyName': 'iam:PassedToService',
            'ContextKeyValues': [targetservice],
            'ContextKeyType': 'string'
        })
    response = iamclient.simulate_principal_policy(
        PolicySourceArn=passer.label,
        ActionNames=['iam:PassRole'],
        ResourceArns=[passed.label],
        ContextEntries=context_entries
    )
    if 'EvaluationResults' in response and 'EvalDecision' in response['EvaluationResults'][0]:
        return response['EvaluationResults'][0]['EvalDecision'] == 'allowed'


# Generic test action, also accepts ResourceArns
def testAction(client, PolicySourceArn, ActionName, ResourceArn=None, ResourcePolicy=None):
    context_response = client.get_context_keys_for_principal_policy(PolicySourceArn=PolicySourceArn)
    context_entries = []
    username_key_used = False
    for key in context_response['ContextKeyNames']:
        # TODO: deal with more context keys
        if key == 'aws:username' and not username_key_used:
            tokens = PolicySourceArn.split('/')
            context_entries.append({
                'ContextKeyName': key,
                'ContextKeyValues': [tokens[len(tokens) - 1]],
                'ContextKeyType': 'string'
            })
            username_key_used = True
            # TODO: Better patch for duplicate context keys
    if ResourceArn is not None:
        response = client.simulate_principal_policy(
            PolicySourceArn=PolicySourceArn,
            # CallerArn=PolicySourceArn,
            ActionNames=[ActionName],
            ResourceArns=[ResourceArn],
            ContextEntries=context_entries,
            # ResourcePolicy=ResourcePolicy
        )
    else:
        response = client.simulate_principal_policy(
            PolicySourceArn=PolicySourceArn,
            # CallerArn=PolicySourceArn,
            ActionNames=[ActionName],
            ContextEntries=context_entries,
            # ResourcePolicy=ResourcePolicy
        )

    if 'EvaluationResults' in response:
        if 'EvalDecision' in response['EvaluationResults'][0]:
            return response['EvaluationResults'][0]['EvalDecision'] == 'allowed'
    raise Exception('Failed to get a response when simulating a policy')


# Tests actions while trying to pull resource policies when applicable
# Returns result from testAction if the service doesn't use resource policies
def getResourcePolicy(session, ResourceArn):
    service = getServiceFromArn(ResourceArn)
    iamclient = session.create_client('iam')
    # bucket policies
    if service == 's3':
        s3client = session.create_client('s3')
        # TODO: Update example policy for s3:GetBucketPolicy
        result = re.match(r'arn:[^:]+:s3:::([^/]+)', ResourceArn)
        if result is None:
            raise ValueError("Invalid S3 bucket or object ARN")
        bucket = result.group(1)
        return s3client.get_bucket_policy(Bucket=bucket)['Policy']
    # key policies
    elif service == 'kms':
        kmsclient = session.create_client('kms')
        # TODO: Update example policy for kms:GetKeyPolicy
        return kmsclient.get_key_policy(KeyId=ResourceArn, PolicyName='default')['Policy']
    # TODO: extend
    else:
        return None


# Grab the service the resource belongs to
# pattern is arn:partition:service:region:account_id:resource
def getServiceFromArn(inputstr):
    tokens = inputstr.split(':')
    if len(tokens) < 6:
        raise ValueError("Invalid ARN")

    return tokens[2]
