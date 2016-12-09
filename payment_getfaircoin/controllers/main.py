# -*- coding: utf-8 -*-
try:
    import simplejson as json
except ImportError:
    import json

import logging
import pprint
import werkzeug

from openerp import http, SUPERUSER_ID
from openerp.http import request

_logger = logging.getLogger(__name__)


class GetfaircoinController(http.Controller):
    _return_url = '/payment/getfaircoin/return'
    _cancel_url = '/payment/getfaircoin/cancel'
    _exception_url = '/payment/getfaircoin/error'
    _reject_url = '/payment/getfaircoin/reject'

    @http.route([
        '/payment/getfaircoin/return',
        '/payment/getfaircoin/cancel',
        '/payment/getfaircoin/error',
        '/payment/getfaircoin/reject',
    ], type='http', auth='none')
    def getfaircoin_return(self, **post):
        _logger.info('Getfaircoin: entering form_feedback with post data %s', pprint.pformat(post))  # debug
        request.registry['payment.transaction'].form_feedback(request.cr, SUPERUSER_ID, post, 'getfaircoin', context=request.context)
        post = dict((key.upper(), value) for key, value in post.items())
        return_url = post.get('ADD_RETURNDATA') or '/'
        return werkzeug.utils.redirect(return_url)
