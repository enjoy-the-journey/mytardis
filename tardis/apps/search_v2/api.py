'''
RESTful API for MyTardis search.
Implemented with Tastypie.

.. moduleauthor:: Manish Kumar <rishimanish123@gmail.com>
'''
import json
import datetime
import pytz

from django.conf import settings

from tastypie import fields
from tastypie.resources import Resource, Bundle
from tastypie.exceptions import BadRequest
from tastypie.serializers import Serializer
from django_elasticsearch_dsl.search import Search
from elasticsearch_dsl import MultiSearch, Q

from tardis.tardis_portal.models import Experiment, DataFile, Dataset
from tardis.tardis_portal.api import default_authentication

LOCAL_TZ = pytz.timezone(settings.TIME_ZONE)


class PrettyJSONSerializer(Serializer):
    json_indent = 2

    def to_json(self, data, options=None):
        options = options or {}
        data = self.to_simple(data, options)
        return json.dumps(data, cls=json.JSONEncoder,
                          sort_keys=True, ensure_ascii=False,
                          indent=self.json_indent) + "\n"


if settings.DEBUG:
    default_serializer = PrettyJSONSerializer()
else:
    default_serializer = Serializer()


class SearchObject(object):
    def __init__(self, hits=None, id=None):
        self.hits = hits
        self.id = id


class SearchAppResource(Resource):
    """Tastypie resource for simple-search"""
    hits = fields.ApiField(attribute='hits', null=True)

    class Meta:
        resource_name = 'simple-search'
        list_allowed_methods = ['get', 'post']
        serializer = default_serializer
        authentication = default_authentication
        object_class = SearchObject
        always_return_data = True

    def detail_uri_kwargs(self, bundle_or_obj):
        kwargs = {}
        if isinstance(bundle_or_obj, Bundle):
            kwargs['pk'] = bundle_or_obj.obj.id
        else:
            kwargs['pk'] = bundle_or_obj['id']

        return kwargs

    def get_object_list(self, bundle):
        user = bundle.request.user
        query_text = bundle.request.GET.get('query', None)
        if not query_text:
            raise BadRequest("Missing query parameter")
        s = Search()
        search = s.query(
            "multi_match",
            query=query_text,
            fields=["title", "description", "filename"]
        )
        total = search.count()
        search = search[0:total]
        results = search.execute()
        result_dict = {k: [] for k in ["experiments", "datasets", "datafiles"]}
        for hit in results.hits.hits:
            if hit["_index"] == "dataset":
                check_dataset_access = filter_dataset_result(hit, user.id)
                if check_dataset_access:
                    result_dict["datasets"].append(hit)

            elif hit["_index"] == "experiments":
                check_experiment_access = filter_experiment_result(hit, user.id)
                if check_experiment_access:
                    result_dict["experiments"].append(hit)

            elif hit["_index"] == "datafile":
                check_datafile_access = filter_datafile_result(hit, user.id)
                if check_datafile_access:
                    result_dict["datafiles"].append(hit)

        return [SearchObject(id=1, hits=result_dict)]

    def obj_get_list(self, bundle, **kwargs):
        return self.get_object_list(bundle)


class AdvanceSearchAppResource(Resource):
    hits = fields.ApiField(attribute='hits', null=True)

    class Meta:
        resource_name = 'advance-search'
        list_allowed_methods = ['get', 'post']
        serializer = default_serializer
        authentication = default_authentication
        object_class = SearchObject
        always_return_data = True

    def detail_uri_kwargs(self, bundle_or_obj):
        kwargs = {}
        if isinstance(bundle_or_obj, Bundle):
            kwargs['pk'] = bundle_or_obj.obj.id
        else:
            kwargs['pk'] = bundle_or_obj['id']

        return kwargs

    def get_object_list(self, bundle):
        return bundle

    def obj_get_list(self, bundle, **kwargs):
        return self.get_object_list(bundle)

    def obj_create(self, bundle, **kwargs):
        bundle = self.dehydrate(bundle)
        return bundle

    def dehydrate(self, bundle):
        user = bundle.request.user
        # if anonymous user search public data only
        query_text = bundle.data.get("text", None)
        type_tag = bundle.data.get("TypeTag", None)
        index_list = []
        for type in type_tag:
            if type == 'Experiment':
                index_list.append('experiments')
            elif type == 'Dataset':
                index_list.append('dataset')
            elif type == 'Datafile':
                index_list.append('datafile')
        end_date = bundle.data.get("EndDate", None)
        start_date = bundle.data.get("StartDate", None)
        if end_date:
            end_date_utc = datetime.datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%S.%fZ")\
                .replace(tzinfo=pytz.timezone('UTC'))
            end_date = end_date_utc.astimezone(LOCAL_TZ).date()
        if start_date:
            start_date_utc = datetime.datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%S.%fZ")\
                .replace(tzinfo=pytz.timezone('UTC'))
            start_date = start_date_utc.astimezone(LOCAL_TZ).date()
        instrument_list = bundle.data.get("InstrumentList", None)
        if instrument_list:
            instrument_list_string = ' '.join(instrument_list)
        # query for experiment model
        ms = MultiSearch(index=index_list)
        if 'experiments' in index_list:
            q = Q("match", title=query_text)
            if (start_date is not None) & (end_date is not None):
                q = q & Q("range", created_time={'gte': start_date, 'lte': end_date})
            ms = ms.add(Search().query(q))
        if 'dataset' in index_list:
            q = Q("match", description=query_text)
            if (start_date is not None) & (end_date is not None):
                q = q & Q("range", created_time={'gte': start_date, 'lte': end_date})
            if instrument_list:
                q = q & Q("match", instrument__name=instrument_list_string)
            # add instrument query
            ms = ms.add(Search().query(q))
        if 'datafile' in index_list:
            q = Q("match", filename=query_text)
            if (start_date is not None) & (end_date is not None):
                q = q & Q("range", created_time={'gte': start_date, 'lte': end_date})
            ms = ms.add(Search().query(q))
        result = ms.execute()
        result_dict = {k: [] for k in ["experiments", "datasets", "datafiles"]}
        for item in result:
            for hit in item.hits.hits:
                if hit["_index"] == "dataset":
                    check_dataset_access = filter_dataset_result(hit, user.id)
                    if check_dataset_access:
                        result_dict["datasets"].append(hit)

                elif hit["_index"] == "experiments":
                    check_experiment_access = filter_experiment_result(hit, user.id)
                    if check_experiment_access:
                        result_dict["experiments"].append(hit)

                elif hit["_index"] == "datafile":
                    check_datafile_access = filter_datafile_result(hit, user.id)
                    if check_datafile_access:
                        result_dict["datafiles"].append(hit)

        if bundle.request.method == 'POST':
            bundle.obj = SearchObject(id=1, hits=result_dict)
        return bundle


def filter_experiment_result(hit, userid):
    exp = Experiment.objects.get(id=hit["_id"])
    return bool(exp.objectacls.filter(entityId=userid).count() > 0)


def filter_dataset_result(hit, userid):
    dataset = Dataset.objects.get(id=hit["_id"])
    exps = dataset.experiments.all()
    for exp in exps:
        if exp.objectacls.filter(entityId=userid).count() > 0:
            return True
    return False


def filter_datafile_result(hit, userid):
    datafile = DataFile.objects.get(id=hit["_id"])
    ds = datafile.dataset
    exps = ds.experiments.all()
    for exp in exps:
        if exp.objectacls.filter(entityId=userid).count() > 0:
            return True
    return False
