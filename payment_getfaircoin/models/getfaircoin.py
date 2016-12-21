# -*- coding: utf-'8' "-*-"
from hashlib import sha1
import logging
import urllib
import urlparse

from openerp.addons.payment.models.payment_acquirer import ValidationError
from openerp.addons.payment_getfaircoin.controllers.main import GetfaircoinController
from openerp.osv import osv, fields
from openerp.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


def normalize_keys_upper(data):
    """Set all keys of a dictionnary to uppercase

    Getfaircoin parameters names are case insensitive
    convert everything to upper case to be able to easily detected the presence
    of a parameter by checking the uppercase key only
    """
    return dict((key.upper(), val) for key, val in data.items())


class AcquirerGetfaircoin(osv.Model):
    _inherit = 'payment.acquirer'

    def _get_getfaircoin_urls(self, cr, uid, environment, context=None):
        """ Getfaircoin URLs
        """
        if environment == 'prod':
            return {
                'getfaircoin_form_url': 'https://getfaircoin.net?edd-listener=fairmarket-JDwMNCEp9D5cqmNgy9Ad',
            }
        else:
            return {
                'getfaircoin_form_url': 'https://getfaircoin.net?edd-listener=fairmarket-JDwMNCEp9D5cqmNgy9Ad',
            }

    def _get_providers(self, cr, uid, context=None):
        providers = super(AcquirerGetfaircoin, self)._get_providers(cr, uid, context=context)
        providers.append(['getfaircoin', 'Getfaircoin'])
        return providers

    _columns = {
        'brq_websitekey': fields.char('WebsiteKey', required_if_provider='getfaircoin'),
        'brq_secretkey': fields.char('SecretKey', required_if_provider='getfaircoin'),
    }

    def _getfaircoin_generate_digital_sign(self, acquirer, inout, values):
        """ Generate the shasign for incoming or outgoing communications.

        :param browse acquirer: the payment.acquirer browse record. It should
                                have a shakey in shaky out
        :param string inout: 'in' (openerp contacting getfaircoin) or 'out' (getfaircoin
                             contacting openerp).
        :param dict values: transaction values

        :return string: shasign
        """
        assert inout in ('in', 'out')
        assert acquirer.provider == 'getfaircoin'

        keys = "add_returndata Brq_amount Brq_culture Brq_currency Brq_invoicenumber Brq_return Brq_returncancel Brq_returnerror Brq_returnreject brq_test Brq_websitekey".split()

        def get_value(key):
            if values.get(key):
                return values[key]
            return ''

        values = dict(values or {})

        if inout == 'out':
            for key in values.keys():
                # case insensitive keys
                if key.upper() == 'BRQ_SIGNATURE':
                    del values[key]
                    break

            items = sorted(values.items(), key=lambda (x, y): x.lower())
            sign = ''.join('%s=%s' % (k, urllib.unquote_plus(v)) for k, v in items)
        else:
            sign = ''.join('%s=%s' % (k,get_value(k)) for k in keys)
        #Add the pre-shared secret key at the end of the signature
        sign = sign + acquirer.brq_secretkey
        if isinstance(sign, str):
            # TODO: remove me? should not be used
            sign = urlparse.parse_qsl(sign)
        shasign = sha1(sign.encode('utf-8')).hexdigest()
        return shasign


    def getfaircoin_form_generate_values(self, cr, uid, id, partner_values, tx_values, context=None):
        _logger.debug("BEGIN getfaircoin_form_generate_values : %s " %(tx_values))
        base_url = self.pool['ir.config_parameter'].get_param(cr, uid, 'web.base.url')
        acquirer = self.browse(cr, uid, id, context=context)
        getfaircoin_tx_values = dict(tx_values)
        getfaircoin_tx_values.update({
            #'Brq_websitekey': acquirer.brq_websitekey,
            #'brq_test': False if acquirer.environment == 'prod' else True,
            'return': '%s' % urlparse.urljoin(base_url, GetfaircoinController._return_url),
            'returncancel': '%s' % urlparse.urljoin(base_url, GetfaircoinController._cancel_url),
            'returnerror': '%s' % urlparse.urljoin(base_url, GetfaircoinController._exception_url),
            'returnreject': '%s' % urlparse.urljoin(base_url, GetfaircoinController._reject_url),
            'order_id': tx_values['reference'],
            'first_name': partner_values['name'],
            'email': partner_values['email'],
            'amount': tx_values['amount'],
            'currency': tx_values['currency'] and tx_values['currency'].name or '',
            'lang':'en', # (partner_values.get('lang') or 'en').replace('_', '-'),
        })
        if getfaircoin_tx_values.get('return_url'):
            getfaircoin_tx_values['add_returndata'] = getfaircoin_tx_values.pop('return_url')
        else: 
            getfaircoin_tx_values['add_returndata'] = ''
        #getfaircoin_tx_values['Brq_signature'] = self._getfaircoin_generate_digital_sign(acquirer, 'in', getfaircoin_tx_values)
        _logger.debug("END getfaircoin_form_generate_values. Returning : %s " %(getfaircoin_tx_values))
        return partner_values, getfaircoin_tx_values

    def getfaircoin_get_form_action_url(self, cr, uid, id, context=None):
        acquirer = self.browse(cr, uid, id, context=context)
        return self._get_getfaircoin_urls(cr, uid, acquirer.environment, context=context)['getfaircoin_form_url']

class TxGetfaircoin(osv.Model):
    _inherit = 'payment.transaction'

    # getfaircoin status
    _getfaircoin_valid_tx_status = [190]
    _getfaircoin_pending_tx_status = [790, 791, 792, 793]
    _getfaircoin_cancel_tx_status = [890, 891]
    _getfaircoin_error_tx_status = [490, 491, 492]
    _getfaircoin_reject_tx_status = [690]

    _columns = {
         'getfaircoin_txnid': fields.char('Transaction ID'),
    }
    

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    def _getfaircoin_form_get_tx_from_data(self, cr, uid, data, context=None):
        """ Given a data dict coming from getfaircoin, verify it and find the related
        transaction record. """
        #origin_data = dict(data)
        data = normalize_keys_upper(data)
        reference, pay_id = data.get('ORDER_ID'), data.get('PAYMENT_STATUS')
        if not reference:
            error_msg = 'Getfaircoin: received data with missing reference (%s) ' % (reference)
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        tx_ids = self.search(cr, uid, [('reference', '=', reference)], context=context)
        if not tx_ids or len(tx_ids) > 1:
            error_msg = 'Getfaircoin: received data for reference %s' % (reference)
            if not tx_ids:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.error(error_msg)
            raise ValidationError(error_msg)
        tx = self.pool['payment.transaction'].browse(cr, uid, tx_ids[0], context=context)

        #verify shasign
        #shasign_check = self.pool['payment.acquirer']._getfaircoin_generate_digital_sign(tx.acquirer_id, 'out', origin_data)
        #if shasign_check.upper() != shasign.upper():
        #    error_msg = 'Getfaircoin: invalid shasign, received %s, computed %s, for data %s' % (shasign, shasign_check, data)
        #    _logger.error(error_msg)
        #    raise ValidationError(error_msg)

        return tx 

    def _getfaircoin_form_get_invalid_parameters(self, cr, uid, tx, data, context=None):
        invalid_parameters = []
        data = normalize_keys_upper(data)
        #if tx.acquirer_reference and data.get('BRQ_TRANSACTIONS') != tx.acquirer_reference:
        #    invalid_parameters.append(('Transaction Id', data.get('BRQ_TRANSACTIONS'), tx.acquirer_reference))
        # check what is buyed
        if float_compare(float(data.get('AMOUNT', '0.0')), tx.amount, 2) != 0:
            invalid_parameters.append(('Amount', data.get('AMOUNT'), '%.2f' % tx.amount))
        #if data.get('BRQ_CURRENCY') != tx.currency_id.name:
        #    invalid_parameters.append(('Currency', data.get('BRQ_CURRENCY'), tx.currency_id.name))

        return invalid_parameters

    def _getfaircoin_form_validate(self, cr, uid, tx, data, context=None):
        _logger.info("getfaircoin_form_validate : %s " %data)
        data = normalize_keys_upper(data)
        data_mod = {'order_id' : data.get('ITEM_NUMBER') }
        status_code = int(data.get('payment_status'))
        if not status_code:
            status_code = 'PENDING'
        tx = self._getfaircoin_form_get_tx_from_data(cr, uid, data_mod, context=context)
        _logger.debug("status_code : %s" %status_code)
        if 'DONE' in status_code:
            tx.write({
                'state': 'done',
                'getfaircoin_txnid': data.get('GETFAIR_ID'),
            })
            return True
        elif 'CANCEL' in status_code:
            tx.write({
                'state': 'pending',
                'getfaircoin_txnid': data.get('GETFAIR_ID'),
            })
            return True
        elif 'PENDING' in status_code:
            tx.write({
                'state': 'cancel',
                'getfaircoin_txnid': data.get('GETFAIR_ID'),
            })
            return True
        else:
            error = 'Getfaircoin: feedback error'
            _logger.info(error)
            tx.write({
                'state': 'error',
                'state_message': error,
                'getfaircoin_txnid': data.get('GETFAIR_ID'),
            })
            return False
