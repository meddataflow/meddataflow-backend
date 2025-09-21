import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"

class Task:
    def __init__(
        self,
        task_id: str,
        task_type: str,
        payload: Dict[str, Any],
        priority: int = 5,
        max_retries: int = 3,
        retry_delay: int = 60
    ):
        self.task_id = task_id
        self.task_type = task_type
        self.payload = payload
        self.priority = priority
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.status = TaskStatus.PENDING
        self.attempts = 0
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.scheduled_at = datetime.utcnow()
        self.result = None
        self.error = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "payload": self.payload,
            "priority": self.priority,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "status": self.status,
            "attempts": self.attempts,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "scheduled_at": self.scheduled_at.isoformat(),
            "result": self.result,
            "error": self.error
        }

class InMemoryQueue:
    """
    Simple in-memory queue for development/testing.
    In production, use Redis or RabbitMQ.
    """
    
    def __init__(self, max_workers: int = 5):
        self.tasks = {}  # task_id -> Task
        self.pending_queue = []  # List of task_ids, sorted by priority and created_at
        self.processing = set()  # Set of task_ids currently being processed
        self.max_workers = max_workers
        self.workers = []
        self.running = False
        self.task_handlers = {}
        
    def register_handler(self, task_type: str, handler):
        """Register a handler function for a specific task type"""
        self.task_handlers[task_type] = handler
        logger.info(f"Registered handler for task type: {task_type}")
    
    async def enqueue(self, task: Task) -> str:
        """Add a task to the queue"""
        self.tasks[task.task_id] = task
        
        # Insert in priority order
        inserted = False
        for i, existing_task_id in enumerate(self.pending_queue):
            existing_task = self.tasks[existing_task_id]
            if (task.priority < existing_task.priority or 
                (task.priority == existing_task.priority and task.created_at < existing_task.created_at)):
                self.pending_queue.insert(i, task.task_id)
                inserted = True
                break
        
        if not inserted:
            self.pending_queue.append(task.task_id)
        
        logger.info(f"Enqueued task {task.task_id} of type {task.task_type}")
        return task.task_id
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID"""
        return self.tasks.get(task_id)
    
    def get_tasks_by_status(self, status: TaskStatus) -> List[Task]:
        """Get all tasks with a specific status"""
        return [task for task in self.tasks.values() if task.status == status]
    
    async def start_workers(self):
        """Start worker coroutines"""
        if self.running:
            return
        
        self.running = True
        self.workers = [
            asyncio.create_task(self._worker(f"worker-{i}"))
            for i in range(self.max_workers)
        ]
        logger.info(f"Started {self.max_workers} queue workers")
    
    async def stop_workers(self):
        """Stop worker coroutines"""
        self.running = False
        
        for worker in self.workers:
            worker.cancel()
        
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        logger.info("Stopped all queue workers")
    
    async def _worker(self, worker_name: str):
        """Worker coroutine that processes tasks"""
        logger.info(f"Started queue worker: {worker_name}")
        
        while self.running:
            try:
                # Get next task
                task_id = await self._get_next_task()
                
                if task_id is None:
                    await asyncio.sleep(1)  # No tasks available, wait
                    continue
                
                task = self.tasks[task_id]
                await self._process_task(worker_name, task)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_name} encountered error: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def _get_next_task(self) -> Optional[str]:
        """Get the next task to process"""
        current_time = datetime.utcnow()
        
        # Look for a task that's ready to be processed
        for i, task_id in enumerate(self.pending_queue):
            if task_id in self.processing:
                continue
            
            task = self.tasks[task_id]
            
            # Check if task is scheduled for the future
            if task.scheduled_at > current_time:
                continue
            
            # Mark as processing and return
            self.pending_queue.pop(i)
            self.processing.add(task_id)
            task.status = TaskStatus.PROCESSING
            task.updated_at = current_time
            
            return task_id
        
        return None
    
    async def _process_task(self, worker_name: str, task: Task):
        """Process a single task"""
        try:
            logger.info(f"{worker_name} processing task {task.task_id} of type {task.task_type}")
            
            task.attempts += 1
            task.updated_at = datetime.utcnow()
            
            # Get handler for task type
            handler = self.task_handlers.get(task.task_type)
            
            if not handler:
                raise Exception(f"No handler registered for task type: {task.task_type}")
            
            # Execute task
            result = await handler(task.payload)
            
            # Mark as completed
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.updated_at = datetime.utcnow()
            
            logger.info(f"{worker_name} completed task {task.task_id}")
            
        except Exception as e:
            logger.error(f"{worker_name} failed processing task {task.task_id}: {e}")
            
            task.error = str(e)
            task.updated_at = datetime.utcnow()
            
            # Check if we should retry
            if task.attempts < task.max_retries:
                task.status = TaskStatus.RETRYING
                task.scheduled_at = datetime.utcnow() + timedelta(seconds=task.retry_delay)
                
                # Re-add to pending queue for retry
                self.pending_queue.append(task.task_id)
                logger.info(f"Scheduled task {task.task_id} for retry {task.attempts}/{task.max_retries}")
            else:
                task.status = TaskStatus.FAILED
                logger.error(f"Task {task.task_id} failed after {task.attempts} attempts")
        
        finally:
            # Remove from processing set
            self.processing.discard(task.task_id)

class QueueService:
    """
    Queue service for background task processing
    """
    
    def __init__(self):
        self.queue = InMemoryQueue()
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """Register default task handlers"""
        self.queue.register_handler("workflow_execution", self._handle_workflow_execution)
        self.queue.register_handler("hl7_processing", self._handle_hl7_processing)
        self.queue.register_handler("data_export", self._handle_data_export)
        self.queue.register_handler("notification", self._handle_notification)
    
    async def start(self):
        """Start the queue service"""
        await self.queue.start_workers()
        logger.info("Queue service started")
    
    async def stop(self):
        """Stop the queue service"""
        await self.queue.stop_workers()
        logger.info("Queue service stopped")
    
    async def enqueue_workflow_execution(
        self,
        workflow_id: str,
        message_id: str,
        raw_message: str,
        vendor_info: Dict[str, Any],
        priority: int = 5
    ) -> str:
        """Enqueue a workflow execution task"""
        
        task_id = f"workflow_{workflow_id}_{message_id}_{uuid.uuid4().hex[:8]}"
        
        payload = {
            "workflow_id": workflow_id,
            "message_id": message_id,
            "raw_message": raw_message,
            "vendor_info": vendor_info,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        task = Task(
            task_id=task_id,
            task_type="workflow_execution",
            payload=payload,
            priority=priority,
            max_retries=3,
            retry_delay=60
        )
        
        await self.queue.enqueue(task)
        return task_id
    
    async def enqueue_hl7_processing(
        self,
        message_id: str,
        raw_message: str,
        processing_config: Dict[str, Any],
        priority: int = 5
    ) -> str:
        """Enqueue an HL7 message processing task"""
        
        task_id = f"hl7_{message_id}_{uuid.uuid4().hex[:8]}"
        
        payload = {
            "message_id": message_id,
            "raw_message": raw_message,
            "processing_config": processing_config,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        task = Task(
            task_id=task_id,
            task_type="hl7_processing",
            payload=payload,
            priority=priority,
            max_retries=2,
            retry_delay=30
        )
        
        await self.queue.enqueue(task)
        return task_id
    
    async def enqueue_data_export(
        self,
        export_type: str,
        export_config: Dict[str, Any],
        tenant_id: str,
        priority: int = 7
    ) -> str:
        """Enqueue a data export task"""
        
        task_id = f"export_{export_type}_{tenant_id}_{uuid.uuid4().hex[:8]}"
        
        payload = {
            "export_type": export_type,
            "export_config": export_config,
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        task = Task(
            task_id=task_id,
            task_type="data_export",
            payload=payload,
            priority=priority,
            max_retries=1,
            retry_delay=120
        )
        
        await self.queue.enqueue(task)
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a task"""
        task = self.queue.get_task(task_id)
        if task:
            return task.to_dict()
        return None
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        pending = len([t for t in self.queue.tasks.values() if t.status == TaskStatus.PENDING])
        processing = len([t for t in self.queue.tasks.values() if t.status == TaskStatus.PROCESSING])
        completed = len([t for t in self.queue.tasks.values() if t.status == TaskStatus.COMPLETED])
        failed = len([t for t in self.queue.tasks.values() if t.status == TaskStatus.FAILED])
        retrying = len([t for t in self.queue.tasks.values() if t.status == TaskStatus.RETRYING])
        
        return {
            "total_tasks": len(self.queue.tasks),
            "pending": pending,
            "processing": processing,
            "completed": completed,
            "failed": failed,
            "retrying": retrying,
            "workers": len(self.queue.workers),
            "running": self.queue.running
        }
    
    # Task Handlers
    async def _handle_workflow_execution(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle workflow execution task"""
        from database.connection import fetch_one, execute
        
        workflow_id = payload["workflow_id"]
        message_id = payload["message_id"]
        raw_message = payload["raw_message"]
        vendor_info = payload["vendor_info"]
        
        try:
            # Get workflow from database
            workflow_query = "SELECT * FROM workflows WHERE id = $1 AND status = 'ACTIVE'"
            workflow = await fetch_one(workflow_query, workflow_id)
            
            if not workflow:
                raise Exception(f"Workflow {workflow_id} not found or not active")
            
            # Update message status
            await execute(
                "UPDATE hl7_messages SET status = 'PROCESSING' WHERE id = $1",
                message_id
            )
            
            
            # Prepare trigger data
            trigger_data = {
                "message_id": message_id,
                "raw_message": raw_message,
                "vendor_info": vendor_info,
                "source": "queue_service"
            }
            
            # Execute workflow (simplified - would need proper workflow object)
            execution_id = f"exec_{uuid.uuid4()}"
            
            # Update message status to processed
            await execute(
                "UPDATE hl7_messages SET status = 'PROCESSED', processed_at = $2 WHERE id = $1",
                message_id,
                datetime.utcnow()
            )
            
            # Update vendor stats
            await execute(
                "UPDATE vendor_endpoints SET total_messages_processed = total_messages_processed + 1 WHERE id = $1",
                vendor_info['vendor_endpoint_id']
            )
            
            return {
                "success": True,
                "execution_id": execution_id,
                "message": "Workflow executed successfully"
            }
            
        except Exception as e:
            # Update message status to failed
            await execute(
                "UPDATE hl7_messages SET status = 'FAILED', processing_errors = $2 WHERE id = $1",
                message_id,
                json.dumps({"error": str(e), "timestamp": datetime.utcnow().isoformat()})
            )
            
            # Update vendor stats
            await execute(
                "UPDATE vendor_endpoints SET total_messages_failed = total_messages_failed + 1 WHERE id = $1",
                vendor_info['vendor_endpoint_id']
            )
            
            raise e
    
    async def _handle_hl7_processing(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle HL7 message processing task"""
        message_id = payload["message_id"]
        raw_message = payload["raw_message"]
        processing_config = payload["processing_config"]
        
        # Simulate HL7 processing
        logger.info(f"Processing HL7 message {message_id}")
        
        # Add processing logic here
        await asyncio.sleep(1)  # Simulate processing time
        
        return {
            "success": True,
            "message_id": message_id,
            "processed_at": datetime.utcnow().isoformat()
        }
    
    async def _handle_data_export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle data export task"""
        export_type = payload["export_type"]
        export_config = payload["export_config"]
        tenant_id = payload["tenant_id"]
        
        # Simulate data export
        logger.info(f"Exporting {export_type} data for tenant {tenant_id}")
        
        # Add export logic here
        await asyncio.sleep(5)  # Simulate export time
        
        return {
            "success": True,
            "export_type": export_type,
            "tenant_id": tenant_id,
            "exported_at": datetime.utcnow().isoformat(),
            "file_url": f"https://exports.example.com/{tenant_id}/{export_type}/{uuid.uuid4()}.csv"
        }
    
    async def _handle_notification(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle notification task"""
        notification_type = payload.get("type", "email")
        recipient = payload.get("recipient")
        message = payload.get("message")
        
        # Simulate sending notification
        logger.info(f"Sending {notification_type} notification to {recipient}")
        
        await asyncio.sleep(0.5)  # Simulate sending time
        
        return {
            "success": True,
            "notification_type": notification_type,
            "recipient": recipient,
            "sent_at": datetime.utcnow().isoformat()
        }

# Global queue service instance
queue_service = QueueService()