import asyncio
import json
import httpx
import websockets
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("Diagnostico", "DEBUG")

async def main():
    headers = {
        "Authorization": f"Bearer {settings.DERIV_API_TOKEN}",
        "Deriv-App-ID": settings.DERIV_APP_ID,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient(base_url=settings.DERIV_REST_BASE_URL, headers=headers) as client:
        resp = await client.post(f"/trading/v1/options/accounts/{settings.DERIV_ACCOUNT_ID}/otp")
        ws_url = resp.json().get('data', {}).get('url', '')
    
    async with websockets.connect(ws_url, ping_interval=30) as ws:
        logger.info("Conectado!")
        
        # Teste 1: proposal com underlying_symbol
        req1 = {
            "proposal": 1,
            "amount": 1.0,
            "basis": "stake",
            "contract_type": "CALL",
            "currency": "USD",
            "duration": 5,
            "duration_unit": "t",
            "underlying_symbol": "R_100",
            "req_id": 1
        }
        await ws.send(json.dumps(req1))
        r1 = json.loads(await ws.recv())
        logger.info(f"PROPOSAL RESULT:\n{json.dumps(r1, indent=2)}")
        
        if 'proposal' in r1:
            proposal_id = r1['proposal']['id']
            ask_price = r1['proposal']['ask_price']
            logger.info(f"PROPOSAL SUCESSO! ID: {proposal_id}, Preco: {ask_price}")
            
            # Teste 2: Buy
            req2 = {
                "buy": proposal_id,
                "price": ask_price,
                "req_id": 2
            }
            await ws.send(json.dumps(req2))
            r2 = json.loads(await ws.recv())
            logger.info(f"BUY RESULT:\n{json.dumps(r2, indent=2)}")
            
            if 'buy' in r2:
                contract_id = r2['buy']['contract_id']
                logger.info(f"BUY SUCESSO! Contract ID: {contract_id}")
                
                # Teste 3: proposal_open_contract
                req3 = {
                    "proposal_open_contract": 1,
                    "contract_id": contract_id,
                    "req_id": 3
                }
                await ws.send(json.dumps(req3))
                r3 = json.loads(await ws.recv())
                logger.info(f"MONITOR RESULT:\n{json.dumps(r3, indent=2)}")

if __name__ == "__main__":
    asyncio.run(main())
