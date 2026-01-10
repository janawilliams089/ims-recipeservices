from typing import List
from datetime import date 
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from database import get_db_connection
import httpx
import logging

# config
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="https://authservices-npr8.onrender.com/auth/token")
router = APIRouter(prefix="/is_addons", tags=["AddOns"])

BLOCKCHAIN_URL = "https://ims-blockchain.onrender.com/blockchain/addon"

# helper to get user ID from token
async def get_user_id_from_token(token: str) -> int:
    USER_SERVICE_ME_URL = "https://authservices-npr8.onrender.com/auth/users/me"
    async with httpx.AsyncClient() as client:
        response = await client.get(USER_SERVICE_ME_URL, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        user_data = response.json()
        return user_data.get("userId")
    
# models
class IngredientOut(BaseModel):
    IngredientID: int
    IngredientName: str

class AddOnCreate(BaseModel):
    AddOnName: str
    IngredientID: int
    Price: float
    Amount: float
    Measurement: str

class AddOnOut(BaseModel):
    AddOnID: int
    AddOnName: str
    IngredientID: int
    IngredientName: str
    Price: float
    Amount: float
    Measurement: str

# auth validation
async def validate_token_and_roles(token: str, allowed_roles: List[str]):
    USER_SERVICE_ME_URL = "https://authservices-npr8.onrender.com/auth/users/me"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(USER_SERVICE_ME_URL, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_detail = f"Auth service error: {e.response.status_code} - {e.response.text}"
            logger.error(error_detail)
            raise HTTPException(status_code=e.response.status_code, detail=error_detail)
        except httpx.RequestError as e:
            logger.error(f"Auth service unavailable: {e}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Auth service unavailable: {e}")

    user_data = response.json()
    if user_data.get("userRole") not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

# create add-on
@router.post("/", response_model=AddOnOut, status_code=status.HTTP_201_CREATED)
async def create_add_on(add_on_data: AddOnCreate, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff"])
    conn = None
    tx_hash_for_response = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM AddOns WHERE AddOnName = ?", add_on_data.AddOnName)
            if await cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An add-on with the name '{add_on_data.AddOnName}' already exists.")
            await cursor.execute("SELECT IngredientName FROM Ingredients WHERE IngredientID = ?", add_on_data.IngredientID)
            ingredient = await cursor.fetchone()
            if not ingredient:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ingredient with ID {add_on_data.IngredientID} not found.")
            await cursor.execute("""
                INSERT INTO AddOns (AddOnName, IngredientID, Price, Amount, Measurement)
                OUTPUT INSERTED.AddOnID VALUES (?, ?, ?, ?, ?)
            """, (add_on_data.AddOnName, add_on_data.IngredientID, add_on_data.Price, add_on_data.Amount, add_on_data.Measurement))
            new_add_on_id = (await cursor.fetchone()).AddOnID
            await conn.commit()

            # log to blockchain (best-effort)
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "CREATE",
                    "user_id": user_id,
                    "AddOnID": new_add_on_id,
                    "AddOnName": add_on_data.AddOnName,
                    "IngredientID": add_on_data.IngredientID,
                    "IngredientName": ingredient.IngredientName,
                    "Price": float(add_on_data.Price),
                    "Amount": float(add_on_data.Amount),
                    "Measurement": add_on_data.Measurement,
                    "old_values": None,
                    "new_values": {
                        "AddOnName": add_on_data.AddOnName,
                        "IngredientID": add_on_data.IngredientID,
                        "IngredientName": ingredient.IngredientName,
                        "Price": float(add_on_data.Price),
                        "Amount": float(add_on_data.Amount),
                        "Measurement": add_on_data.Measurement
                    }
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        BLOCKCHAIN_URL,
                        json=block_payload,
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    if resp.status_code in (200, 201):
                        try:
                            resp_json = resp.json()
                            tx_hash_for_response = resp_json.get("tx_hash") or resp_json.get("txHash") or resp_json.get("tx")
                        except Exception:
                            tx_hash_for_response = None
            except Exception as e:
                logger.error(f"Blockchain add-ons log failed: {str(e)}")

            return AddOnOut(
                AddOnID=new_add_on_id, AddOnName=add_on_data.AddOnName, IngredientID=add_on_data.IngredientID,
                IngredientName=ingredient.IngredientName, Price=add_on_data.Price, Amount=add_on_data.Amount,
                Measurement=add_on_data.Measurement
            )
    finally:
        if conn: await conn.close()

# get all add-ons
@router.get("/", response_model=List[AddOnOut])
async def get_all_addons(token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff", "cashier", "user"])
    conn = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT a.AddOnID, a.AddOnName, a.IngredientID, i.IngredientName, a.Price, a.Amount, a.Measurement
                FROM AddOns a JOIN Ingredients i ON a.IngredientID = i.IngredientID ORDER BY a.AddOnName
            """)
            rows = await cursor.fetchall()

            result = []
            for row in rows:
                result.append(AddOnOut(
                    AddOnID=row.AddOnID,
                    AddOnName=row.AddOnName,
                    IngredientID=row.IngredientID,
                    IngredientName=row.IngredientName,
                    Price=float(row.Price),
                    Amount=float(row.Amount),
                    Measurement=row.Measurement
                ))
            return result
    finally:
        if conn: await conn.close()

# delete add-on
@router.delete("/{add_on_id}", status_code=status.HTTP_200_OK)
async def delete_add_on(add_on_id: int, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager"])
    conn = None
    tx_hash_for_response = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            # get old values for blockchain logging
            await cursor.execute("SELECT AddOnName, IngredientID, Price, Amount, Measurement FROM AddOns WHERE AddOnID = ?", add_on_id)
            old = await cursor.fetchone()
            if not old:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Add-on with ID {add_on_id} not found.")
            await cursor.execute("SELECT IngredientName FROM Ingredients WHERE IngredientID = ?", old.IngredientID)
            old_ingredient = await cursor.fetchone()
            old_values = {
                "AddOnName": old.AddOnName,
                "IngredientID": old.IngredientID,
                "IngredientName": old_ingredient.IngredientName if old_ingredient else "",
                "Price": float(old.Price),
                "Amount": float(old.Amount),
                "Measurement": old.Measurement
            }

            delete_op = await cursor.execute("DELETE FROM AddOns WHERE AddOnID = ?", add_on_id)
            if delete_op.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Add-on with ID {add_on_id} not found.")
            await conn.commit()

            # log to blockchain
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "DELETE",
                    "user_id": user_id,
                    "AddOnID": add_on_id,
                    "AddOnName": old.AddOnName,
                    "IngredientID": old.IngredientID,
                    "IngredientName": old_ingredient.IngredientName if old_ingredient else "",
                    "Price": float(old.Price),
                    "Amount": float(old.Amount),
                    "Measurement": old.Measurement,
                    "old_values": old_values,
                    "new_values": None
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        BLOCKCHAIN_URL,
                        json=block_payload,
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    if resp.status_code in (200, 201):
                        try:
                            resp_json = resp.json()
                            tx_hash_for_response = resp_json.get("tx_hash") or resp_json.get("txHash") or resp_json.get("tx")
                        except Exception:
                            tx_hash_for_response = None
            except Exception as e:
                logger.error(f"Blockchain add-ons log failed: {str(e)}")

        result = {"message": f"Add-on with ID {add_on_id} successfully deleted."}
        if tx_hash_for_response:
            result["tx_hash"] = tx_hash_for_response
        return result
    finally:
        if conn: await conn.close()

# get all ingredients for add-ons
@router.get("/ingredients/", response_model=List[IngredientOut], tags=["Ingredients"])
async def get_all_ingredients_for_addons(token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff", "cashier", "user"])
    conn = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT IngredientID, IngredientName FROM Ingredients ORDER BY IngredientName")
            rows = await cursor.fetchall()
            return rows
    finally:
        if conn: await conn.close()

# update add-on
@router.put("/{add_on_id}", response_model=AddOnOut, status_code=status.HTTP_200_OK)
async def update_add_on(add_on_id: int, add_on_data: AddOnCreate, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff"])
    conn = None
    tx_hash_for_response = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            # get old values for blockchain logging
            await cursor.execute("SELECT AddOnName, IngredientID, Price, Amount, Measurement FROM AddOns WHERE AddOnID = ?", add_on_id)
            old = await cursor.fetchone()
            await cursor.execute("SELECT IngredientName FROM Ingredients WHERE IngredientID = ?", old.IngredientID)
            old_ingredient = await cursor.fetchone()
            old_values = {
                "AddOnName": old.AddOnName,
                "IngredientID": old.IngredientID,
                "IngredientName": old_ingredient.IngredientName if old_ingredient else "",
                "Price": float(old.Price),
                "Amount": float(old.Amount),
                "Measurement": old.Measurement
            }

            await cursor.execute("SELECT 1 FROM AddOns WHERE AddOnID = ?", add_on_id)
            if not await cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Add-on with ID {add_on_id} not found.")

            await cursor.execute("SELECT 1 FROM AddOns WHERE AddOnName = ? AND AddOnID != ?", add_on_data.AddOnName, add_on_id)
            if await cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An add-on with the name '{add_on_data.AddOnName}' already exists.")

            await cursor.execute("SELECT IngredientName FROM Ingredients WHERE IngredientID = ?", add_on_data.IngredientID)
            ingredient = await cursor.fetchone()
            if not ingredient:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ingredient with ID {add_on_data.IngredientID} not found.")

            # Update the add-on
            await cursor.execute("""
                UPDATE AddOns SET AddOnName = ?, IngredientID = ?, Price = ?, Amount = ?, Measurement = ?
                WHERE AddOnID = ?
            """, (add_on_data.AddOnName, add_on_data.IngredientID, add_on_data.Price, add_on_data.Amount, add_on_data.Measurement, add_on_id))

            await conn.commit()

            # log to blockchain
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "UPDATE",
                    "user_id": user_id,
                    "AddOnID": add_on_id,
                    "AddOnName": add_on_data.AddOnName,
                    "IngredientID": add_on_data.IngredientID,
                    "IngredientName": ingredient.IngredientName,
                    "Price": float(add_on_data.Price),
                    "Amount": float(add_on_data.Amount),
                    "Measurement": add_on_data.Measurement,
                    "old_values": old_values,
                    "new_values": {
                        "AddOnName": add_on_data.AddOnName,
                        "IngredientID": add_on_data.IngredientID,
                        "IngredientName": ingredient.IngredientName,
                        "Price": float(add_on_data.Price),
                        "Amount": float(add_on_data.Amount),
                        "Measurement": add_on_data.Measurement
                    }
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        BLOCKCHAIN_URL,
                        json=block_payload,
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    if resp.status_code in (200, 201):
                        try:
                            resp_json = resp.json()
                            tx_hash_for_response = resp_json.get("tx_hash") or resp_json.get("txHash") or resp_json.get("tx")
                        except Exception:
                            tx_hash_for_response = None
            except Exception as e:
                logger.error(f"Blockchain add-ons log failed: {str(e)}")

            result = AddOnOut(
                AddOnID=add_on_id,
                AddOnName=add_on_data.AddOnName,
                IngredientID=add_on_data.IngredientID,
                IngredientName=ingredient.IngredientName,
                Price=add_on_data.Price,
                Amount=add_on_data.Amount,
                Measurement=add_on_data.Measurement
            )
            if tx_hash_for_response:
                return {"addon": result, "tx_hash": tx_hash_for_response}
            return result
    finally:
        if conn: await conn.close()