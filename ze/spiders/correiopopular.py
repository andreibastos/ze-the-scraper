# -*- coding: utf-8 -*-

#TODO:articleBody datePublished dateModified
from ze.spiders import ZeSpider

class CorreioPopular(ZeSpider):
    #tudo parece OK

    name = 'correiopopular'
    allowed_domains = ['correio.rac.com.br']
    parses = [{
        "ze.items.creativework.ArticleItem": {
            "fields": {
                "name": [
                    "meta[property='og:title']::attr(content)",
                    "meta[name=title]::attr(content)",
                    '[itemprop=headline]::text',
                    '.news-title::text'
                ],
                "image": [
                    'meta[property="og:image"]::attr(content)',
                    '[itemprop="image"] img::attr(src)',
                    '#foto_auto img::attr(src)'
                ],
                "description": [
                    "meta[property='og:description']::attr(content)",
                    "meta[name=description]::attr(content)",
                    '[itemprop=description]::attr(content)',
                    '[itemprop=description]::text',
                    '.resumo h2::text'
                ],
                "author": [
                    '[itemprop=author]::text',
                    '.publish-by b::text'
                ],
                "datePublished": [
                    '[itemprop=datePublished]::attr(content)',
                    '.publish-time::text',#dateModified ta junto
                ],
                "dateModified": [
                    '[itemprop=dateModified]::attr(content)',
                    '.node-body p em::text'#ta com datePublished
                ],
                "articleBody": [
                    '[itemprop=articleBody]',
                    '.article-container'#get text from all div.fe-content
                ],
                "keywords": [
                    '[itemprop=keywords] a::text',
                    '.tags-container a::text',
                ]
            }
        }
    }]