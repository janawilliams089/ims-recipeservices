from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import List, Optional
import httpx
from database import get_db_connection
import logging

# config
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:4000/auth/token")
router = APIRouter()

BLOCKCHAIN_URL = "http://localhost:8006/blockchain/recipe"

async def get_user_id_from_token(token: str) -> int:
    USER_SERVICE_ME_URL = "http://localhost:4000/auth/users/me"
    async with httpx.AsyncClient() as client:
        response = await client.get(USER_SERVICE_ME_URL, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        user_data = response.json()
        return user_data.get("userId")

# models
class IngredientInRecipe(BaseModel):
    IngredientID: int
    Amount: float
    Measurement: str

class MaterialInRecipe(BaseModel):
    MaterialID: int
    Quantity: float
    Measurement: str

class RecipeCreate(BaseModel):
    ProductID: int
    RecipeName: str
    Ingredients: List[IngredientInRecipe]
    Materials: List[MaterialInRecipe]
    AddOns: List[int]

class RecipeUpdate(RecipeCreate):
    ProductID: int
    RecipeName: str
    Ingredients: List[IngredientInRecipe]
    Materials: List[MaterialInRecipe]
    AddOns: List[int]  

class RecipeOut(BaseModel):
    RecipeID: int
    ProductID: int
    RecipeName: str
    Ingredients: List[dict]
    Materials: List[dict]
    AddOns: List[dict]

# auth validation
async def validate_token_and_roles(token: str, allowed_roles: List[str]):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get("http://localhost:4000/auth/users/me", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        except httpx.RequestError:
             raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication service is unavailable")

    user_data = response.json()
    user_role = user_data.get("userRole")
    if user_role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: Your role does not have permission for this action.")

# get all recipes
@router.get("/", response_model=List[RecipeOut])
async def get_all_recipes(token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff", "cashier", "user"])
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT RecipeID, ProductID, RecipeName FROM Recipes")
            recipes = await cursor.fetchall()
            result = []
            for recipe in recipes:
                # fetch ingredients
                await cursor.execute("""
                    SELECT ri.RecipeIngredientID, i.IngredientName, ri.Amount, ri.Measurement 
                    FROM RecipeIngredients ri JOIN Ingredients i ON ri.IngredientID = i.IngredientID 
                    WHERE ri.RecipeID = ?
                """, (recipe.RecipeID,))
                ingredients = await cursor.fetchall()

                # fetch materials
                await cursor.execute("""
                    SELECT rm.RecipeMaterialID, m.MaterialName, rm.Quantity, rm.Measurement 
                    FROM RecipeMaterials rm JOIN Materials m ON rm.MaterialID = m.MaterialID 
                    WHERE rm.RecipeID = ?
                """, (recipe.RecipeID,))
                materials = await cursor.fetchall()

                # fetch the add-ons for the current recipe
                await cursor.execute("""
                    SELECT ra.AddOnID, a.AddOnName
                    FROM RecipeAddOns ra
                    JOIN AddOns a ON ra.AddOnID = a.AddOnID
                    WHERE ra.RecipeID = ?
                """, (recipe.RecipeID,))
                addons = await cursor.fetchall()

                result.append({
                    "RecipeID": recipe.RecipeID,
                    "ProductID": recipe.ProductID,
                    "RecipeName": recipe.RecipeName,
                    "Ingredients": [{"RecipeIngredientID": row.RecipeIngredientID, "IngredientName": row.IngredientName, "Amount": row.Amount, "Measurement": row.Measurement} for row in ingredients],
                    "Materials": [{"RecipeMaterialID": row.RecipeMaterialID, "MaterialName": row.MaterialName, "Quantity": row.Quantity, "Measurement": row.Measurement} for row in materials],
                    "AddOns": [{"AddOnID": row.AddOnID, "AddOnName": row.AddOnName} for row in addons]
                })
        return result
    finally:
        if conn: await conn.close()

# get recipe details by ID
@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: int, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff", "cashier", "user"])
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # fetch recipe, ingredients, and materials 
            await cursor.execute("SELECT RecipeID, ProductID, RecipeName FROM Recipes WHERE RecipeID = ?", (recipe_id,))
            recipe = await cursor.fetchone()
            if not recipe:
                raise HTTPException(status_code=404, detail="Recipe not found")

            await cursor.execute("""
                SELECT ri.RecipeIngredientID, i.IngredientName, ri.Amount, ri.Measurement 
                FROM RecipeIngredients ri JOIN Ingredients i ON ri.IngredientID = i.IngredientID 
                WHERE ri.RecipeID = ?
            """, (recipe_id,))
            ingredients = await cursor.fetchall()

            await cursor.execute("""
                SELECT rm.RecipeMaterialID, m.MaterialName, rm.Quantity, rm.Measurement 
                FROM RecipeMaterials rm JOIN Materials m ON rm.MaterialID = m.MaterialID 
                WHERE rm.RecipeID = ?
            """, (recipe_id,))
            materials = await cursor.fetchall()
            
            # fetch the add-ons for the specific recipe
            await cursor.execute("""
                SELECT ra.AddOnID, a.AddOnName
                FROM RecipeAddOns ra
                JOIN AddOns a ON ra.AddOnID = a.AddOnID
                WHERE ra.RecipeID = ?
            """, (recipe_id,))
            addons = await cursor.fetchall()

            # update the return object to include AddOns
            return {
                "RecipeID": recipe.RecipeID,
                "ProductID": recipe.ProductID,
                "RecipeName": recipe.RecipeName,
                "Ingredients": [{"RecipeIngredientID": row.RecipeIngredientID, "IngredientName": row.IngredientName, "Amount": row.Amount, "Measurement": row.Measurement} for row in ingredients],
                "Materials": [{"RecipeMaterialID": row.RecipeMaterialID, "MaterialName": row.MaterialName, "Quantity": row.Quantity, "Measurement": row.Measurement} for row in materials],
                "AddOns": [{"AddOnID": row.AddOnID, "AddOnName": row.AddOnName} for row in addons]
            }
    finally:
        if conn: await conn.close()

# create recipe
@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_recipe(recipe: RecipeCreate, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff"])
    conn = await get_db_connection()
    tx_hash_for_response: Optional[str] = None
    try:
        async with conn.cursor() as cursor:
            # duplicate check
            await cursor.execute("SELECT 1 FROM Recipes WHERE RecipeName COLLATE Latin1_General_CI_AS = ?", recipe.RecipeName)
            if await cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail="A recipe with this name already exists."
                )       
            # create recipe record
            await cursor.execute(
                "INSERT INTO Recipes (ProductID, RecipeName) OUTPUT INSERTED.RecipeID VALUES (?, ?)", 
                recipe.ProductID, 
                recipe.RecipeName
            )
            new_id_row = await cursor.fetchone()
            if not new_id_row:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                    detail="Failed to create the recipe record."
                )
            new_recipe_id = new_id_row.RecipeID
            # insert all ingredients 
            for ing in recipe.Ingredients:
                await cursor.execute(
                    "INSERT INTO RecipeIngredients (RecipeID, IngredientID, Amount, Measurement) VALUES (?, ?, ?, ?)", 
                    new_recipe_id, 
                    ing.IngredientID, 
                    ing.Amount, 
                    ing.Measurement
                )
            # insert all materials
            for mat in recipe.Materials:
                await cursor.execute(
                    "INSERT INTO RecipeMaterials (RecipeID, MaterialID, Quantity, Measurement) VALUES (?, ?, ?, ?)", 
                    new_recipe_id, 
                    mat.MaterialID, 
                    mat.Quantity, 
                    mat.Measurement
                )
            # insert all add-ons
            if recipe.AddOns: # ensure the list is not empty
                for addon_id in recipe.AddOns:
                    await cursor.execute(
                        "INSERT INTO RecipeAddOns (RecipeID, AddOnID) VALUES (?, ?)", 
                        new_recipe_id, 
                        addon_id
                    )
            await conn.commit()      

            # log to blockchain
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "CREATE",
                    "user_id": user_id,
                    "RecipeID": new_recipe_id,
                    "ProductID": recipe.ProductID,
                    "RecipeName": recipe.RecipeName,
                    "Ingredients": [{"IngredientID": ing.IngredientID, "Amount": float(ing.Amount), "Measurement": ing.Measurement} for ing in recipe.Ingredients],
                    "Materials": [{"MaterialID": mat.MaterialID, "Quantity": float(mat.Quantity), "Measurement": mat.Measurement} for mat in recipe.Materials],
                    "AddOns": [{"AddOnID": aid} for aid in recipe.AddOns],
                    "old_values": None,
                    "new_values": {
                        "ProductID": recipe.ProductID,
                        "RecipeName": recipe.RecipeName,
                        "Ingredients": [{"IngredientID": ing.IngredientID, "Amount": float(ing.Amount), "Measurement": ing.Measurement} for ing in recipe.Ingredients],
                        "Materials": [{"MaterialID": mat.MaterialID, "Quantity": float(mat.Quantity), "Measurement": mat.Measurement} for mat in recipe.Materials],
                        "AddOns": [{"AddOnID": aid} for aid in recipe.AddOns]
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
                logger.error(f"Blockchain recipe log failed: {str(e)}")
     
            result = {"message": "Recipe created successfully", "RecipeID": new_recipe_id}
            if tx_hash_for_response:
                result["tx_hash"] = tx_hash_for_response
            return result

    except Exception as e:
        # if any step fails, roll back
        await conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while creating the recipe: {e}"
        )
    finally:
        if conn:
            await conn.close()

# update recipe
@router.put("/{recipe_id}", response_model=dict)
async def update_recipe(recipe_id: int, recipe: RecipeUpdate, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff"])
    conn = await get_db_connection()
    tx_hash_for_response: Optional[str] = None
    try:
        async with conn.cursor() as cursor:
            # get old values for blockchain logging
            await cursor.execute("SELECT ProductID, RecipeName FROM Recipes WHERE RecipeID = ?", recipe_id)
            old_recipe = await cursor.fetchone()
            await cursor.execute("SELECT IngredientID, Amount, Measurement FROM RecipeIngredients WHERE RecipeID = ?", recipe_id)
            old_ingredients = [{"IngredientID": row.IngredientID, "Amount": float(row.Amount), "Measurement": row.Measurement} for row in await cursor.fetchall()]
            await cursor.execute("SELECT MaterialID, Quantity, Measurement FROM RecipeMaterials WHERE RecipeID = ?", recipe_id)
            old_materials = [{"MaterialID": row.MaterialID, "Quantity": float(row.Quantity), "Measurement": row.Measurement} for row in await cursor.fetchall()]
            await cursor.execute("SELECT AddOnID FROM RecipeAddOns WHERE RecipeID = ?", recipe_id)
            old_addons = [{"AddOnID": row.AddOnID} for row in await cursor.fetchall()]
            old_values = {
                "ProductID": old_recipe.ProductID,
                "RecipeName": old_recipe.RecipeName,
                "Ingredients": old_ingredients,
                "Materials": old_materials,
                "AddOns": old_addons
            }

            await cursor.execute(
                "SELECT 1 FROM Recipes WHERE RecipeName COLLATE Latin1_General_CI_AS = ? AND RecipeID != ?", 
                recipe.RecipeName, recipe_id
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Recipe name already exists.")

            await cursor.execute(
                "UPDATE Recipes SET ProductID = ?, RecipeName = ? WHERE RecipeID = ?", 
                recipe.ProductID, recipe.RecipeName, recipe_id
            )

            # delete and reinsert ingredients
            await cursor.execute("DELETE FROM RecipeIngredients WHERE RecipeID = ?", recipe_id)
            for ing in recipe.Ingredients:
                await cursor.execute(
                    "INSERT INTO RecipeIngredients (RecipeID, IngredientID, Amount, Measurement) VALUES (?, ?, ?, ?)", 
                    recipe_id, ing.IngredientID, ing.Amount, ing.Measurement
                )

            # delete and reinsert materials
            await cursor.execute("DELETE FROM RecipeMaterials WHERE RecipeID = ?", recipe_id)
            for mat in recipe.Materials:
                await cursor.execute(
                    "INSERT INTO RecipeMaterials (RecipeID, MaterialID, Quantity, Measurement) VALUES (?, ?, ?, ?)", 
                    recipe_id, mat.MaterialID, mat.Quantity, mat.Measurement
                )

            # delete and reinsert add-ons
            await cursor.execute("DELETE FROM RecipeAddOns WHERE RecipeID = ?", recipe_id)
            if recipe.AddOns:
                for addon_id in recipe.AddOns:
                    await cursor.execute(
                        "INSERT INTO RecipeAddOns (RecipeID, AddOnID) VALUES (?, ?)", 
                        recipe_id, addon_id
                    )

            await conn.commit()

            # log to blockchain
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "UPDATE",
                    "user_id": user_id,
                    "RecipeID": recipe_id,
                    "ProductID": recipe.ProductID,
                    "RecipeName": recipe.RecipeName,
                    "Ingredients": [{"IngredientID": ing.IngredientID, "Amount": float(ing.Amount), "Measurement": ing.Measurement} for ing in recipe.Ingredients],
                    "Materials": [{"MaterialID": mat.MaterialID, "Quantity": float(mat.Quantity), "Measurement": mat.Measurement} for mat in recipe.Materials],
                    "AddOns": [{"AddOnID": aid} for aid in recipe.AddOns],
                    "old_values": old_values,
                    "new_values": {
                        "ProductID": recipe.ProductID,
                        "RecipeName": recipe.RecipeName,
                        "Ingredients": [{"IngredientID": ing.IngredientID, "Amount": float(ing.Amount), "Measurement": ing.Measurement} for ing in recipe.Ingredients],
                        "Materials": [{"MaterialID": mat.MaterialID, "Quantity": float(mat.Quantity), "Measurement": mat.Measurement} for mat in recipe.Materials],
                        "AddOns": [{"AddOnID": aid} for aid in recipe.AddOns]
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
                logger.error(f"Blockchain recipe log failed (update): {str(e)}")

            result = {"message": "Recipe updated successfully"}
            if tx_hash_for_response:
                result["tx_hash"] = tx_hash_for_response
            return result
    finally:
        if conn: await conn.close()

# delete recipe
@router.delete("/{recipe_id}", response_model=dict)
async def delete_recipe(recipe_id: int, token: str = Depends(oauth2_scheme)):
    await validate_token_and_roles(token, ["admin", "manager", "staff"])
    conn = await get_db_connection()
    tx_hash_for_response: Optional[str] = None
    try:
        async with conn.cursor() as cursor:
            # get old values for blockchain logging
            await cursor.execute("SELECT ProductID, RecipeName FROM Recipes WHERE RecipeID = ?", recipe_id)
            old_recipe = await cursor.fetchone()
            await cursor.execute("SELECT IngredientID, Amount, Measurement FROM RecipeIngredients WHERE RecipeID = ?", recipe_id)
            old_ingredients = [{"IngredientID": row.IngredientID, "Amount": float(row.Amount), "Measurement": row.Measurement} for row in await cursor.fetchall()]
            await cursor.execute("SELECT MaterialID, Quantity, Measurement FROM RecipeMaterials WHERE RecipeID = ?", recipe_id)
            old_materials = [{"MaterialID": row.MaterialID, "Quantity": float(row.Quantity), "Measurement": row.Measurement} for row in await cursor.fetchall()]
            await cursor.execute("SELECT AddOnID FROM RecipeAddOns WHERE RecipeID = ?", recipe_id)
            old_addons = [{"AddOnID": row.AddOnID} for row in await cursor.fetchall()]
            old_values = {
                "ProductID": old_recipe.ProductID,
                "RecipeName": old_recipe.RecipeName,
                "Ingredients": old_ingredients,
                "Materials": old_materials,
                "AddOns": old_addons
            }

            await cursor.execute("DELETE FROM RecipeIngredients WHERE RecipeID = ?", (recipe_id,))
            await cursor.execute("DELETE FROM RecipeMaterials WHERE RecipeID = ?", (recipe_id,))
            delete_op = await cursor.execute("DELETE FROM Recipes WHERE RecipeID = ?", (recipe_id,))
            
            if delete_op.rowcount == 0:
                raise HTTPException(status_code=404, detail="Recipe not found.")
            
            await conn.commit()

            # log to blockchain
            try:
                user_id = await get_user_id_from_token(token)
                block_payload = {
                    "action": "DELETE",
                    "user_id": user_id,
                    "RecipeID": recipe_id,
                    "ProductID": old_recipe.ProductID,
                    "RecipeName": old_recipe.RecipeName,
                    "Ingredients": old_ingredients,
                    "Materials": old_materials,
                    "AddOns": old_addons,
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
                logger.error(f"Blockchain recipe log failed (delete): {str(e)}")

            result = {"message": "Recipe deleted successfully"}
            if tx_hash_for_response:
                result["tx_hash"] = tx_hash_for_response
            return result
    finally:
        if conn: await conn.close()