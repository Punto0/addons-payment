Proceso de instalación de merchants.py:

1. Instalamos sqlite3: $sudo apt-get install sqlite3 

2. Creamos una base de datos sqlite: > sqlite3 /path/to/database/file
				     sqlite3> .quit

3. Instalamos electrum-fair (copiado de electrum.fair-coin.org)

Open a terminal window.

Install the following packages on your system: sudo apt-get install python-pip python-qt4

To install type: sudo pip install https://electrum.fair-coin.org/download/Electrum-fair-2.3.3.tar.gz

4. Configurar los datos de la nueva cartera --> Esto todavía está por definir, hay consideraciones de seguridad que tener en cuenta...

5. Con los datos anteriores y con las direcciones de retorno del módulo de odoo payment_faircoin configuramos merchant.conf

6. Damos permisos de ejecución al script > chmod +x merchant.py
   Arrancamos el demonio  > merchant.py
   Chequeamos que va bien > python merchant.py request 0.1 2 120

sudo pip install jsonrpclib


   
