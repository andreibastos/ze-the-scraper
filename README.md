
## Using

### Spider
```shell
scrapy crawl [SPIDER] -a search="-a search='{ \
  "engine": "google", \
  "query": "educação", \
  "last_update": "24H",\
  "results_per_page": 50,\
  "pages": 2 \
}'" 

[SPIDER]
 g1
 ig 
 veja
 g1
```

## References

 - http://xpo6.com/list-of-english-stop-words/
 - [Scrapy - Docs | Jobs: pausing and resuming crawls](https://doc.scrapy.org/en/latest/topics/jobs.html?highlight=scheduler)
 - [scrapy.extensions.memusage][https://github.com/scrapy/scrapy/blob/master/scrapy/extensions/memusage.py]
   It's a good code to extend, overide `_send_report_` function to send to another services than only mail

## Ideas

### Relation DB Schema

https://cloud.google.com/bigtable/docs/schema-design

| Row key | Column data |
| INEP | NEWS:EDUCACAO (V1 03/01/15):558.40 | 

Use this:
- https://blog.scrapinghub.com/2016/04/20/scrapy-tips-from-the-pros-april-2016-edition/
- https://helpdesk.scrapinghub.com/support/solutions/articles/22000200401-dotscrapy-persistence-addon
- https://helpdesk.scrapinghub.com/support/solutions/articles/22000200418-magic-fields-addon
- https://helpdesk.scrapinghub.com/support/solutions/articles/22000200411-delta-fetch-addon
- 
### lambda

```python

class AVRO_FIELD_TYPE(Enum):
    str = 'STRING'
    list = 'RECORD'
    int = 'INTERGE'
    bool = 'BOOLEAN'

f_avro = lambda ft, md='NULLABLE', fd=[]: { 'avro': { 
    # 'field_type': ft.uppe() if ft else AVRO_FIELD_TYPE[type(ft)], 
    'field_type': ft.uppe(), 
    'mode': md, 
    'fields': fd } }

@property
def identifier(self):
    self['output_processor'] = self.get('output_processor') if self.get('output_processor') \
                                else TakeFirst()
    if not hasattr(self, 'schemas'):
        self['schemas'] = self.f_avro('STRING', 'NULLABLE', [])
    
    return self 

@identifier.setter
def identifier(self, value):
    self['output_processor'] if self.get('output_processor') else TakeFirst()
    return self 
```