import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn
import hashlib
from langchain_openai import ChatOpenAI
from langchain_community.document_loaders import UnstructuredPDFLoader
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain.chains.conversation.memory import ConversationBufferMemory
from langchain.chains import ConversationChain
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

DOCUMENT_PATH = "docs/Solar_guide.pdf"
HASH_FILE = "document_hash.txt"
VECTOR_STORE_PATH = "faiss_vector_db"

# Initialize Conversation Buffer Memory
conversation_memory = ConversationBufferMemory()

# Define retriever as a global variable
retriever = None

# Function to check if the document has changed
def document_changed():
    document_mtime = os.path.getmtime(DOCUMENT_PATH1)  # Get the document's last modified time

    if os.path.exists(HASH_FILE):  # Reuse the HASH_FILE for storing the modification time
        with open(HASH_FILE, 'r') as f:
            stored_mtime = f.read()

        # If the modification time is the same, no need to reprocess the document
        if str(document_mtime) == stored_mtime:
            return False  # Document hasn't changed

    # Document has changed, update the stored modification time
    with open(HASH_FILE, 'w') as f:
        f.write(str(document_mtime))

    return True

# Function to load and ingest documents
def ingest_docs():
    # Load the document
    loader = UnstructuredPDFLoader(DOCUMENT_PATH)
    docs = loader.load()

    # Split the documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_documents(docs)

    # Initialize OpenAI Embeddings
    embeddings = OpenAIEmbeddings()

    # Check if chunks are created correctly
    if not chunks:
        raise ValueError("No chunks created from the document. Check the document loading process.")

    # Create or overwrite the vector store
    vectorstore = FAISS.from_documents(documents=chunks, embedding=embeddings)
    vectorstore.save_local(VECTOR_STORE_PATH)

    print("Document successfully ingested into knowledge base")

    return vectorstore  # Return the vectorstore to update retriever globally


# Check if document has changed and ingest if needed
if document_changed():
    print("Document changed. Ingesting new document...")
    conversation_memory.clear()
    conversation_memory = ConversationBufferMemory()
    print("Conversation memory cleared")
    # Ingest the new documents and update the retriever
    vectorstore = ingest_docs()
    retriever = vectorstore.as_retriever()  # Update the global retriever after ingestion
else:
    print("Document unchanged. Loading existing vector store.")
    vectorstore = FAISS.load_local(VECTOR_STORE_PATH, OpenAIEmbeddings(), allow_dangerous_deserialization=True)
    retriever = vectorstore.as_retriever()  # Set retriever during initialization

# Initialize the Conversation Chain with memory
conversation_chain = ConversationChain(
    llm=llm,
    memory=conversation_memory,
)

# Initialize FastAPI application
app = FastAPI(
    title="LangChain Server with Knowledge Base",
    version="1.0",
    description="A knowledge-based API server using LangChain and  a knowledge source",
)

# Define the persona/behavior prompt
persona_prompt = ("""
    "You are a friendly representative of Kay Soley, knowledgeable about solar energy. Answer in the same language as the user. Don't say Hello. Your goal is to engage in a natural conversation, and answer based on the Solar Guide any questions the user may have. Do not ask for personal information at this stage.\n If a question cannot be asnwered by the content of the Solar Guide, say that you are unsure and that the user should ask this question to one of our Technicians during a telephone or home appointment.\n Clarity and Conciseness: Use bullet points or numbered lists for clarity in your responses, and keep responses concise, limited to 2-3 sentences."

""")

@app.get("/")
async def read_root():
    return {
        "message": "Welcome to the LangChain Server with a knowledge base!"
    }

# Function to clear the .cache directory
def clear_cache():
    cache_path = ".cache/*"  # Path to the cache directory
    try:
        os.system(f"rm -rf {cache_path}")
        #logger.info(".cache directory has been cleared.")
    except Exception as e:
        #logger.error(f"Error while clearing cache: {e}")

# Function to clear both conversation memory and cache
def reset_conversation_and_cache():
    global conversation_memory
    conversation_memory.clear()
    #logger.info("Conversation memory has been cleared.")

    # Clear cache directory
    clear_cache()

# Background task to clear memory and cache every 20 minutes
async def periodic_reset():
    while True:
        await asyncio.sleep(3600 * 6)  # Wait for 20 minutes
        reset_conversation_and_cache()

@app.on_event("startup")
async def startup_event():
    # Start the background task when the FastAPI app starts
    asyncio.create_task(periodic_reset())

@app.post("/chat")
async def chat(request: Request):
    input_data = await request.json()
    input_text = input_data.get("input")

    if not input_text:
        return {"error": "No input provided"}

    print(f"Query received: {input_text}")

    try:
        # Retrieve relevant knowledge from the vectorstore
        context = retriever.get_relevant_documents(input_text)
        context_text = "\n\n".join([doc.page_content for doc in context])

        # Print the context_text for debugging
        print(f"Context retrieved: {context_text}")

        # Combine the persona prompt, context, and user query
        question_with_context = f"{persona_prompt}\n\nContext:\n{context_text}\n\nQuestion: {input_text}"

        # Get the response from the LLM using the conversation chain
        result = conversation_chain.run(question_with_context)
        print(f"LLM response: {result}")
    
        conversation_memory.save_context({"input": input_text}, {"output": result})
        
        # Return the LLM's response
        return {"answer": result}
    except Exception as e:
        print(f"Error during chat invocation: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    logger.info("Starting the FastAPI server...")
    port = int(os.getenv('PORT', 80))
    uvicorn.run(app, host="0.0.0.0", port=port)

