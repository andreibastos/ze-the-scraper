# -*- coding: utf-8 -*-
from urllib.parse import urlparse

import logging; logger = logging.getLogger(__name__)

from datetime import datetime
import dateparser

__all__ = ('CleanString', 'FormatString', 'ValidURL', 'ParseDate')


class CleanString(object):

    def __call__(self, value, loader_context):
        return value.strip().strip('\t\n')


class FormatString(object):

    def __call__(self, values, loader_context):
        format_string = loader_context.get('format', '{}')
        for value in values:
            yield format_string.format(value)


class ValidURL(object):

    def __call__(self, value, loader_context):
        try:
            result = urlparse(value)
            if all((result.scheme, result.netloc, result.path)):
                return value
            else:
                return None
        except:
            return None


class ParseDate(object):

    def __init__(self, field):
        self.field = field

    def __call__(self, value, loader_context):
        spider_name = loader_context.get('spider_name')

        if spider_name == 'r7':
            value=value.split('(')[1].split(')')[0]

        if spider_name == 'correiobraziliense':
            return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

        if spider_name == 'bbc':
            return datetime.fromtimestamp(int(value))

        if spider_name == 'mundoeducacao':
            value = value.replace('em','')
            return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

        if (self.field == 'datePublished'):
            if spider_name == 'zh':
                value=value.split('|')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

            if spider_name == 'diariodepernambuco':
                return dateparser.parse(value)

            if spider_name =='correiopopular':
                value=value.split('Atualizado')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')\
                                            .replace('Publicado','')\
                                            .replace('Atualizado','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name=='jconline':
                value=value.split('Atualizado')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')\
                                            .replace('Publicado','')\
                                            .replace('Atualizado','')\
                                            .replace('em','')\
                                            .strip(',')\
                                            .replace('às','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name =='atarde':
                value=value.split('|')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name == 'veja':
                value = value.split(' - ')[1].replace('Publicado','')\
                                            .replace('em','')\
                                            .replace(',','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name=='estadao':
                value1 = value.split('|')[1]
                value1 = value1.replace('h',':')
                value = value.split('|')[0]+value1
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name=='tvcultura':
                value=value.replace('<small>','').replace('</small>','').replace('<time>','').replace('</time>','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name == 'epoca':
                value = value.split(' - Atualizado')[0].replace('h',':')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            # if spider_name == 'globo':
                # return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})



            if spider_name =='exame':
                return dateparser.parse(value)
            if spider_name =='sbt':
                return dateparser.parse(value)
            if spider_name =='sejabixo':
                return dateparser.parse(value.split('em ')[1])
            if spider_name =='senado':
                value = value.split(' - ')[0]
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})



                #GOVERNAMENTAL - ESTADOS
            if spider_name == 'govac':
                if 'Criado' in value:
                    value=value.split(',')[1]
                else:
                    value=value.split(',')[0]
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

            if spider_name == 'govce':
                value=value.split(',')[0]
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

            if spider_name == 'govgo':
                if '-' in value:
                    value=value.split('publicação:')[1].replace('-','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

            if spider_name == 'govpa':
                print('- - - - - - - - - - ', value)
                # value=value.split('publicação:')[1].replace('-','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})


            if spider_name == 'govpb':
                value=value.split('Fotos')[0].replace(' - ',' ')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})



        if (self.field == 'dateModified'):
            if spider_name == 'zh':
                value=value.split('|')[1].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')\
                                            .replace('Atualizada','')\
                                            .replace('em','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})

            # if spider_name == 'globo':
            #     value=value.split('|')[1].replace(' - ',' ')\
            #                                 .replace('h', ':') \
            #                                 .replace('min', '')\
            #                                 .replace('Atualizada','')\
            #                                 .replace('em','')
            #     return dateparser.parse(value, settings={'TIMEZONE': '+0300','DATE_ORDER': 'DMY'})


            if spider_name == 'diariodepernambuco':
                return dateparser.parse(value)

            if spider_name =='correiopopular':
                value=value.split('Atualizado')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')\
                                            .replace('Publicado','')\
                                            .replace('Atualizado','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name=='jconline':
                value=value.split('Atualizado')[0].replace(' - ',' ')\
                                            .replace('h', ':') \
                                            .replace('min', '')\
                                            .replace('Publicado','')\
                                            .replace('Atualizado','')\
                                            .replace('em','')\
                                            .strip(',')\
                                            .replace('às','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})
            if spider_name =='senado':
                value = value.split(' - ')[1].replace('ATUALIZADO EM','')
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})

            if spider_name=='govce':
                value=value.split('em')[1]
                return dateparser.parse(value, settings={'TIMEZONE': '+0300'})


        value = value.replace('Atualizado:', '') \
                     .replace('Atualizado', '') \
                     .replace(' | ', ' ') \
                     .replace('h', ':') \
                     .replace('h ', ':') \
                     .replace(', ', ' ') \
                     .replace('  ', ' ') \
                     .strip()

        try:
            return dateparser.parse(value, settings={'TIMEZONE': '+0300'})
        except Exception as e:
            logger.warning('Date not processed: %s' % value)
            return None

        return value
