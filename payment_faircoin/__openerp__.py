# -*- coding: utf-8 -*-

{
    'name': 'Faircoin-electrum Payment Acquirer',
    'category': 'Hidden',
    'summary': 'Faircoin Payment Acquirer based on electrum wallet',
    'description': """This module permits Faircoin Payment in odoo. It is based on electrum wallet and it needs the merchant.py daemon running in the background. When a payment has been confirmed the daemon transfer the funds automatically to the seller minus a merket fee that is forwarded to other address. Adjust the fee and the market address in merchant.conf""",
    'author': 'Punto0 - Fair Coop',
    'depends': ['payment','sale'],
    'data': [
        'views/faircoin.xml',
        'views/payment_acquirer.xml',
        'views/res_config_view.xml',
        'views/res_company.xml', 
        'data/faircoin.xml',
    ],
    'installable': True,
}
